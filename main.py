import os
import json
import requests
from datetime import datetime, timedelta
from upstash_redis import Redis

# --- 1. پیکربندی و احراز هویت ---
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
CHAT_ID = os.getenv('CHAT_ID', '').strip()
UPSTASH_URL = os.getenv('UPSTASH_URL', '').strip()
UPSTASH_TOKEN = os.getenv('UPSTASH_TOKEN', '').strip()

# آستانه‌های هشدار قیمت (تومان) - پیش‌فرض (اگر در Variables نباشد)
GOLD_PRICE_THRESHOLD = float(os.getenv('GOLD_PRICE_THRESHOLD', 20000000))
SILVER_PRICE_THRESHOLD = float(os.getenv('SILVER_PRICE_THRESHOLD', 600000))

# آستانه هشدار درصد سود/زیان پرتفو (مثلاً اگر سود > 20% یا زیان < -10%)
PORTFOLIO_PROFIT_THRESHOLD = float(os.getenv('PORTFOLIO_PROFIT_THRESHOLD', 20.0))
PORTFOLIO_LOSS_THRESHOLD = float(os.getenv('PORTFOLIO_LOSS_THRESHOLD', -10.0))

# --- 2. ورودی‌های پرتفو (دیگر نیازی به ویرایش کد نیست!) ---
# این مقادیر را در GitHub Secrets یا Variables با نام‌های مشخص شده وارد کنید.
# اگر وارد نکنید، مقادیر پیش‌فرض زیر استفاده می‌شوند (که احتمالاً غلط هستند).
try:
    PF_GOLD_QTY = float(os.getenv('PF_GOLD_QTY', 0))
    PF_GOLD_AVG = float(os.getenv('PF_GOLD_AVG', 0))
    PF_SILVER_QTY = float(os.getenv('PF_SILVER_QTY', 0))
    PF_SILVER_AVG = float(os.getenv('PF_SILVER_AVG', 0))
except ValueError:
    PF_GOLD_QTY, PF_GOLD_AVG, PF_SILVER_QTY, PF_SILVER_AVG = 0, 0, 0, 0

# اتصال به Redis
redis_client = None
try:
    if UPSTASH_URL and UPSTASH_TOKEN:
        redis_client = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
        redis_client.ping()
        print("✅ Connected to Upstash Redis")
except Exception as e:
    print(f"❌ Redis Connection Failed: {e}")

# --- توابع کمکی ---

def get_tehran_time():
    """دریافت زمان فعلی به وقت تهران"""
    utc_now = datetime.utcnow()
    return utc_now + timedelta(hours=3, minutes=30)

def send_telegram_alert(message):
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("📩 Alert sent.")
    except:
        pass

def fetch_asset_data(asset_name):
    """دریافت قیمت و تغییرات روزانه از API کاریزما"""
    url = f"https://inv.charisma.ir/pub/Plans/{asset_name}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json().get('data', {})
        
        # استخراج قیمت لحظه‌ای
        price_rial = 0
        daily_change_percent = 0.0
        
        if 'latestIndexPrice' in data:
            price_rial = float(data['latestIndexPrice'].get('index', 0))
            # استخراج درصد تغییرات (فیلد value معمولاً درصد تغییر است)
            # اگر عدد اعشاری کوچک بود (مثل 0.001)، ضربدر 100 می‌کنیم تا درصد شود
            raw_change = float(data['latestIndexPrice'].get('value', 0))
            if abs(raw_change) < 10: 
                daily_change_percent = raw_change * 100
            else:
                daily_change_percent = raw_change
                
        if price_rial == 0:
            print(f"⚠️ No price found for {asset_name}")
            return None
            
        return {
            "price_rial": price_rial,
            "daily_change": daily_change_percent
        }
    except Exception as e:
        print(f"❌ Error fetching {asset_name}: {e}")
        return None

def calculate_metrics(current_price, buy_avg, qty, asset_name):
    """محاسبه دقیق سود، زیان، کارمزد و نقطه سر‌به‌سر"""
    if qty == 0 or buy_avg == 0:
        return None
        
    current_value = current_price * qty
    total_cost = buy_avg * qty
    
    # کارمزد فروش 1% از ارزش فعلی
    fee = current_value * 0.01
    net_value = current_value - fee
    
    net_profit = net_value - total_cost
    npl_percent = (net_profit / total_cost) * 100 if total_cost > 0 else 0
    
    # نقطه سر‌به‌سر: قیمتی که در آن (قیمت * تعداد) - 1% کارمزد = هزینه کل
    # P * Q * 0.99 = Total_Cost  =>  P = Total_Cost / (Q * 0.99)
    break_even_price = total_cost / (qty * 0.99)
    
    return {
        "current_value": round(current_value, 2),
        "net_profit": round(net_profit, 2),
        "npl_percent": round(npl_percent, 2),
        "break_even_price": round(break_even_price, 2),
        "fee_amount": round(fee, 2)
    }

# --- منطق اصلی ---

def main():
    print("🚀 Starting Charisma Advanced Monitor...")
    timestamp = get_tehran_time().isoformat()
    tehran_time_str = get_tehran_time().strftime("%Y/%m/%d, %H:%M:%S")
    
    alerts = []
    
    # 1. دریافت داده‌های بازار
    gold_data = fetch_asset_data("Gold")
    silver_data = fetch_asset_data("Silver")
    
    if not gold_data or not silver_data:
        print("⛔ Failed to fetch live data. Exiting.")
        return

    # تبدیل به تومان و اعمال ضریب (طلا: تقسیم بر 10 و ضربدر 0.75 برای معادل 18 عیار)
    # نکته: اگر API مستقیماً قیمت طرح را می‌دهد، شاید ضریب 0.75 نیاز نباشد.
    # اما طبق فرمول قبلی شما عمل می‌کنیم.
    gold_price_toman = (gold_data['price_rial'] / 10.0) * 0.75
    silver_price_toman = silver_data['price_rial'] / 10.0
    
    gold_change = gold_data['daily_change']
    silver_change = silver_data['daily_change']

    print(f"💰 Gold: {gold_price_toman:,.0f} T ({gold_change:.2f}%) | Silver: {silver_price_toman:,.0f} T ({silver_change:.2f}%)")

    # 2. بررسی هشدارهای قیمت
    if gold_price_toman >= GOLD_PRICE_THRESHOLD:
        msg = f"🔔 **هشدار قیمت طلا**: عبور از سقف {GOLD_PRICE_THRESHOLD:,.0f}\nقیمت فعلی: {gold_price_toman:,.0f} تومان"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "gold", "message": msg})
    
    if silver_price_toman >= SILVER_PRICE_THRESHOLD:
        msg = f"🔔 **هشدار قیمت نقره**: عبور از سقف {SILVER_PRICE_THRESHOLD:,.0f}\nقیمت فعلی: {silver_price_toman:,.0f} تومان"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "silver", "message": msg})

    # 3. محاسبات پرتفو (فقط اگر مقادیر ورودی وجود داشته باشد)
    portfolio_summary = {}
    if PF_GOLD_QTY > 0 and PF_SILVER_QTY > 0:
        gold_metrics = calculate_metrics(gold_price_toman, PF_GOLD_AVG, PF_GOLD_QTY, "Gold")
        silver_metrics = calculate_metrics(silver_price_toman, PF_SILVER_AVG, PF_SILVER_QTY, "Silver")
        
        total_invested = (PF_GOLD_AVG * PF_GOLD_QTY) + (PF_SILVER_AVG * PF_SILVER_QTY)
        total_current_val = gold_metrics['current_value'] + silver_metrics['current_value']
        total_net_profit = gold_metrics['net_profit'] + silver_metrics['net_profit']
        total_npl_percent = (total_net_profit / total_invested) * 100 if total_invested > 0 else 0
        
        portfolio_summary = {
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_current_val, 2),
            "total_net_profit": round(total_net_profit, 2),
            "total_npl_percent": round(total_npl_percent, 2),
            "assets": {
                "gold": {
                    "qty": PF_GOLD_QTY,
                    "buy_avg": PF_GOLD_AVG,
                    "current_price": gold_price_toman,
                    "daily_change_percent": round(gold_change, 2),
                    "metrics": gold_metrics
                },
                "silver": {
                    "qty": PF_SILVER_QTY,
                    "buy_avg": PF_SILVER_AVG,
                    "current_price": silver_price_toman,
                    "daily_change_percent": round(silver_change, 2),
                    "metrics": silver_metrics
                }
            }
        }
        
        print(f"📊 Portfolio NPL: {total_npl_percent:.2f}% (Profit: {total_net_profit:,.0f})")

        # 4. بررسی هشدارهای درصد سود/زیان
        if total_npl_percent >= PORTFOLIO_PROFIT_THRESHOLD:
            msg = f"🎉 **هشدار سود پرتفو**: سود شما به **{total_npl_percent:.2f}%** رسید!\n(آستانه: {PORTFOLIO_PROFIT_THRESHOLD}%)"
            send_telegram_alert(msg)
            alerts.append({"type": "profit_target", "message": msg})
        
        elif total_npl_percent <= PORTFOLIO_LOSS_THRESHOLD:
            msg = f"📉 **هشدار زیان پرتفو**: زیان شما به **{total_npl_percent:.2f}%** رسید.\n(آستانه: {PORTFOLIO_LOSS_THRESHOLD}%)"
            send_telegram_alert(msg)
            alerts.append({"type": "loss_limit", "message": msg})
            
    else:
        print("⚠️ Portfolio inputs missing. Set PF_GOLD_QTY, etc. in GitHub Variables.")
        portfolio_summary = {"error": "Missing portfolio inputs"}

    # 5. ساخت خروجی نهایی JSON
    final_payload = {
        "last_updated_fa": tehran_time_str,
        "last_updated_iso": timestamp,
        "market_status": "open",
        "assets_summary": {
            "gold": {
                "price_toman": round(gold_price_toman, 2),
                "daily_change_percent": round(gold_change, 2)
            },
            "silver": {
                "price_toman": round(silver_price_toman, 2),
                "daily_change_percent": round(silver_change, 2)
            }
        },
        "portfolio": portfolio_summary,
        "alerts": alerts
    }

    # ذخیره در Redis
    if redis_client:
        try:
            redis_client.set("latest_market_data", json.dumps(final_payload))
            # افزودن به تاریخچه برای نمودار آینده
            history_item = {
                "time": timestamp,
                "gold": gold_price_toman,
                "silver": silver_price_toman,
                "npl": portfolio_summary.get("total_npl_percent", 0)
            }
            redis_client.lpush("market_history", json.dumps(history_item))
            redis_client.ltrim("market_history", 0, 99) # نگهداری 100 رکورد آخر
            print("💾 Data & History saved to Redis.")
        except Exception as e:
            print(f"❌ Redis Error: {e}")

    # تولید فایل JSON
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_payload, f, ensure_ascii=False, indent=2)
    
    print("✅ Execution completed successfully.")

if __name__ == "__main__":
    main()