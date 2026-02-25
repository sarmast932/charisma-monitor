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

# آستانه‌های هشدار قیمت (تومان)
GOLD_PRICE_HIGH = float(os.getenv('GOLD_PRICE_HIGH', 26000000))
GOLD_PRICE_LOW = float(os.getenv('GOLD_PRICE_LOW', 24000000))
SILVER_PRICE_HIGH = float(os.getenv('SILVER_PRICE_HIGH', 600000))
SILVER_PRICE_LOW = float(os.getenv('SILVER_PRICE_LOW', 550000))

# آستانه هشدار درصد سود/زیان پرتفو
PORTFOLIO_PROFIT_THRESHOLD = float(os.getenv('PORTFOLIO_PROFIT_THRESHOLD', 20.0))
PORTFOLIO_LOSS_THRESHOLD = float(os.getenv('PORTFOLIO_LOSS_THRESHOLD', -10.0))

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
    """ارسال پیام به تلگرام"""
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("📩 Alert sent to Telegram.")
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

def get_portfolio_from_redis():
    """دریافت اطلاعات پرتفو از Redis"""
    if not redis_client:
        return None, None, None, None
    try:
        portfolio_data = redis_client.get("user_portfolio")
        if portfolio_data:
            data = json.loads(portfolio_data)
            print(f"📊 Portfolio loaded from Redis: {data}")
            return (
                data.get('gold_qty', 0),
                data.get('gold_avg', 0),
                data.get('silver_qty', 0),
                data.get('silver_avg', 0)
            )
    except Exception as e:
        print(f"⚠️ Error reading portfolio from Redis: {e}")
    return None, None, None, None

def get_portfolio_from_env():
    """دریافت اطلاعات پرتفو از GitHub Variables"""
    try:
        gold_qty = float(os.getenv('PF_GOLD_QTY', 0))
        gold_avg = float(os.getenv('PF_GOLD_AVG', 0))
        silver_qty = float(os.getenv('PF_SILVER_QTY', 0))
        silver_avg = float(os.getenv('PF_SILVER_AVG', 0))
        if gold_qty > 0 or silver_qty > 0:
            print(f"📊 Portfolio loaded from GitHub Variables")
            return gold_qty, gold_avg, silver_qty, silver_avg
    except ValueError:
        pass
    return None, None, None, None

def fetch_asset_data(asset_name):
    """دریافت قیمت و تغییرات روزانه از API کاریزما"""
    url = f"https://inv.charisma.ir/pub/Plans/{asset_name}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json().get('data', {})
        
        price_rial = 0
        daily_change_percent = 0.0
        
        if 'latestIndexPrice' in data:
            price_rial = float(data['latestIndexPrice'].get('index', 0))
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
    fee = current_value * 0.01
    net_value = current_value - fee
    net_profit = net_value - total_cost
    npl_percent = (net_profit / total_cost) * 100 if total_cost > 0 else 0
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
        if redis_client:
            cached = redis_client.get("latest_market_data")
            if cached:
                with open("market_data.json", "w", encoding="utf-8") as f:
                    f.write(cached)
                print("📄 Wrote cached data to JSON.")
        return

    # --- تبدیل قیمت‌ها ---
    # طلای 24 عیار (قیمت خام از API - تقسیم بر 10 برای تومان)
    gold_24k_toman = gold_data['price_rial'] / 10.0
    
    # طلای 18 عیار (ضربدر 0.75 برای تبدیل 24 به 18 عیار)
    gold_18k_toman = gold_24k_toman * 0.75
    
    # نقره (تقسیم بر 10 برای تومان)
    silver_toman = silver_data['price_rial'] / 10.0
    
    gold_change = gold_data['daily_change']
    silver_change = silver_data['daily_change']

    print(f"💰 Gold 24K: {gold_24k_toman:,.0f} T | Gold 18K: {gold_18k_toman:,.0f} T ({gold_change:.2f}%)")
    print(f"💰 Silver: {silver_toman:,.0f} T ({silver_change:.2f}%)")

    # 2. بررسی هشدارهای قیمت (بر اساس طلای 18 عیار)
    if gold_18k_toman >= GOLD_PRICE_HIGH:
        msg = f"🔺 **هشدار طلا (سقف)**: قیمت به {gold_18k_toman:,.0f} رسید"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "gold", "message": msg})
    
    if gold_18k_toman <= GOLD_PRICE_LOW:
        msg = f"🔻 **هشدار طلا (کف)**: قیمت به {gold_18k_toman:,.0f} رسید"
        send_telegram_alert(msg)
        alerts.append({"type": "price_low", "asset": "gold", "message": msg})

    if silver_toman >= SILVER_PRICE_HIGH:
        msg = f"🔺 **هشدار نقره (سقف)**: قیمت به {silver_toman:,.0f} رسید"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "silver", "message": msg})
    
    if silver_toman <= SILVER_PRICE_LOW:
        msg = f"🔻 **هشدار نقره (کف)**: قیمت به {silver_toman:,.0f} رسید"
        send_telegram_alert(msg)
        alerts.append({"type": "price_low", "asset": "silver", "message": msg})

    # 3. دریافت پرتفو (اولویت با Redis است، سپس GitHub Variables)
    gold_qty, gold_avg, silver_qty, silver_avg = get_portfolio_from_redis()
    if gold_qty is None:
        gold_qty, gold_avg, silver_qty, silver_avg = get_portfolio_from_env()
    
    # 4. محاسبات پرتفو (بر اساس طلای 18 عیار)
    portfolio_summary = {}
    if (gold_qty and gold_avg) or (silver_qty and silver_avg):
        gold_metrics = calculate_metrics(gold_18k_toman, gold_avg or 0, gold_qty or 0, "Gold") if gold_qty and gold_avg else None
        silver_metrics = calculate_metrics(silver_toman, silver_avg or 0, silver_qty or 0, "Silver") if silver_qty and silver_avg else None
        
        total_invested = 0
        total_current_val = 0
        total_net_profit = 0
        
        if gold_metrics:
            total_invested += (gold_avg * gold_qty)
            total_current_val += gold_metrics['current_value']
            total_net_profit += gold_metrics['net_profit']
        
        if silver_metrics:
            total_invested += (silver_avg * silver_qty)
            total_current_val += silver_metrics['current_value']
            total_net_profit += silver_metrics['net_profit']
        
        total_npl_percent = (total_net_profit / total_invested) * 100 if total_invested > 0 else 0
        
        portfolio_summary = {
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_current_val, 2),
            "total_net_profit": round(total_net_profit, 2),
            "total_npl_percent": round(total_npl_percent, 2),
            "assets": {}
        }
        
        if gold_metrics:
            portfolio_summary["assets"]["gold"] = {
                "qty": gold_qty,
                "buy_avg": gold_avg,
                "current_price_18k": gold_18k_toman,
                "current_price_24k": gold_24k_toman,
                "daily_change_percent": round(gold_change, 2),
                "metrics": gold_metrics
            }
        
        if silver_metrics:
            portfolio_summary["assets"]["silver"] = {
                "qty": silver_qty,
                "buy_avg": silver_avg,
                "current_price": silver_toman,
                "daily_change_percent": round(silver_change, 2),
                "metrics": silver_metrics
            }
        
        print(f"📊 Portfolio NPL: {total_npl_percent:.2f}%")

        # 5. هشدارهای درصد سود/زیان
        if total_npl_percent >= PORTFOLIO_PROFIT_THRESHOLD:
            msg = f"🎉 **هشدار سود پرتفو**: سود به **{total_npl_percent:.2f}%** رسید."
            send_telegram_alert(msg)
            alerts.append({"type": "profit_target", "message": msg})
        
        elif total_npl_percent <= PORTFOLIO_LOSS_THRESHOLD:
            msg = f"📉 **هشدار زیان پرتفو**: زیان به **{total_npl_percent:.2f}%** رسید."
            send_telegram_alert(msg)
            alerts.append({"type": "loss_limit", "message": msg})
    else:
        print("⚠️ No portfolio data available")
        portfolio_summary = {"message": "Portfolio not configured."}

    # 6. خروجی نهایی
    final_payload = {
        "last_updated_fa": tehran_time_str,
        "last_updated_iso": timestamp,
        "market_status": "open",
        "assets_summary": {
            "gold": {
                "price_24k_toman": round(gold_24k_toman, 2),
                "price_18k_toman": round(gold_18k_toman, 2),
                "daily_change_percent": round(gold_change, 2)
            },
            "silver": {
                "price_toman": round(silver_toman, 2),
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
            history_item = {"time": timestamp, "gold_18k": gold_18k_toman, "gold_24k": gold_24k_toman, "silver": silver_toman, "npl": portfolio_summary.get("total_npl_percent", 0)}
            redis_client.lpush("market_history", json.dumps(history_item))
            redis_client.ltrim("market_history", 0, 99)
            print("💾 Data saved to Redis.")
        except Exception as e:
            print(f"❌ Redis Error: {e}")

    # تولید فایل JSON برای GitHub Pages
    try:
        with open("market_data.json", "w", encoding="utf-8") as f:
            json.dump(final_payload, f, ensure_ascii=False, indent=2)
        print("📄 market_data.json generated successfully.")
    except Exception as e:
        print(f"❌ File Write Error: {e}")
    
    print("✅ Execution completed.")

if __name__ == "__main__":
    main()