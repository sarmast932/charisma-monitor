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

# آستانه‌های هشدار قیمت (تومان) - خواندن از Variables گیت‌هاب
GOLD_PRICE_THRESHOLD = float(os.getenv('GOLD_PRICE_THRESHOLD', 20000000))
SILVER_PRICE_THRESHOLD = float(os.getenv('SILVER_PRICE_THRESHOLD', 600000))

# آستانه هشدار درصد سود/زیان پرتفو
PORTFOLIO_PROFIT_THRESHOLD = float(os.getenv('PORTFOLIO_PROFIT_THRESHOLD', 20.0))
PORTFOLIO_LOSS_THRESHOLD = float(os.getenv('PORTFOLIO_LOSS_THRESHOLD', -10.0))

# --- 2. ورودی‌های پرتفو (خواندن از Variables گیت‌هاب) ---
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
            # تبدیل به درصد اگر عدد اعشاری کوچک باشد
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
    gold_price_toman = (gold_data['price_rial'] / 10.0) * 0.75
    silver_price_toman = silver_data['price_rial'] / 10.0
    
    gold_change = gold_data['daily_change']
    silver_change = silver_data['daily_change']

    print(f"💰 Gold: {gold_price_toman:,.0f} T ({gold_change:.2f}%) | Silver: {silver_price_toman:,.0f} T ({silver_change:.2f}%)")

    # 2. بررسی هشدارهای قیمت (دو طرفه: بیشتر و کمتر از آستانه)
    # طلا
    if gold_price_toman >= GOLD_PRICE_THRESHOLD:
        msg = f"🔺 **هشدار قیمت طلا**: عبور از سقف {GOLD_PRICE_THRESHOLD:,.0f}\nقیمت فعلی: {gold_price_toman:,.0f} تومان"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "gold", "message": msg})
    elif gold_price_toman <= (GOLD_PRICE_THRESHOLD * 0.90): # مثال: 10% کمتر از آستانه هم هشدار دهد
        # یا می‌توانید یک آستانه پایین جداگانه تعریف کنید. فعلاً به صورت نسبی چک می‌کنیم.
        # برای دقت بیشتر، بهتر است متغیر GOLD_PRICE_LOW_THRESHOLD تعریف کنید.
        # اما طبق درخواست شما "کمتر شدن از ترشلد" را اینجا پوشش می‌دهیم اگر بخواهید دقیق باشید:
        pass 
        
    # برای پیاده‌سازی دقیق "کمتر شدن از یک عدد مشخص"، باید متغیر جدیدی تعریف کنید.
    # اما چون فرمودید "کمتر شدن از ترشلد"، فرض را بر این می‌گیریم که ترشلد یک محدوده است یا منظور شکست حمایت است.
    # بیایید فرض کنیم اگر قیمت از ترشلد تعیین شده کمتر شد هم هشدار دهد (شکست حمایت):
    if gold_price_toman < GOLD_PRICE_THRESHOLD:
         # این شرط همیشه برقرار است مگر اینکه قیمت بالا رفته باشد. 
         # منظور شما احتمالاً این است که اگر قیمت از یک "کف" تعیین شده کمتر شد.
         # چون متغیر جداگانه‌ای ندادید، من منطق را اینگونه می‌چینم:
         # اگر قیمت از ترشلد (که فرض می‌کنیم مقاومت است) کمتر شد، هشدار نده (مگر اینکه شکست حمایت مد نظر باشد).
         # برای سادگی و جلوگیری از اسپم، فقط عبور رو به بالا و عبور رو به پایین از یک کف فرضی را چک می‌کنیم.
         # اما بهترین کار تعریف دو متغیر است. فعلاً فقط عبور رو به بالا را داریم.
         # *اصلاحیه*: طبق دستور شما "کمتر شدن از ترشلد" هم اضافه شود.
         # فرض می‌کنیم ترشلد وارد شده یک "باند بالایی" است و ما نیاز به "باند پایینی" نداریم؟
         # خیر، معمولاً ترشلد یک عدد است. اگر قیمت از آن کمتر شد یعنی چه؟ یعنی زیر مقاومت است.
         # احتمالاً منظور شما این است: اگر قیمت از ترشلدِ سود (مثلاً 3 میلیون) کمتر شد (ریزش کرد) هشدار بده.
         # پس ما یک آستانه پایین هم نیاز داریم. اما برای رعایت سادگی و عدم تغییر زیاد در Variables:
         # من شرط را اینگونه می‌گذارم: اگر قیمت از ترشلد تعیین شده **عبور کرد** (بالا یا پایین).
         # یعنی اگر قبلاً بالا بوده و حالا آمده پایین، یا برعکس.
         # اما چون وضعیت قبلی را در این اجرا نداریم، ساده‌ترین حالت:
         # هشدار اگر قیمت > ترشلد (سقف) OR قیمت < (ترشلد * 0.9) (کف نسبی).
         pass

    # پیاده‌سازی دقیق درخواست: هشدار اگر قیمت از ترشلد تعیین شده کمتر شد (شکست حمایت)
    # برای این کار باید یک متغیر دیگر داشته باشیم یا فرض کنیم ترشلد ورودی کاربر یک عدد رند است و هر دو طرف مهم است.
    # بیایید یک متغیر جدید به نام GOLD_LOW_THRESHOLD در کد فرض کنیم که اگر نبود، همان ترشلد بالا ملاک است؟ نه.
    # راه حل استاندارد: دو متغیر در گیت‌هاب تعریف کنید: GOLD_HIGH_THRESHOLD و GOLD_LOW_THRESHOLD.
    # اما چون نمی‌خواهم شما را مجبور به تعریف متغیر جدید کنم، از همان GOLD_PRICE_THRESHOLD به عنوان "مقاومت" استفاده می‌کنم
    # و برای "حمایت" یک مقدار پیش‌فرض (مثلاً 5% کمتر) در نظر می‌گیرم یا کلاً این بخش را منوط به تعریف متغیر دوم می‌کنم.
    
    # *تصمیم نهایی برای کد*: من دو متغیر جدید در کد تعریف می‌کنم که اگر در گیت‌هاب نبودند، از همان ترشلد اصلی استفاده کنند (که عملاً یعنی فقط یک طرفه).
    # اما برای اینکه دقیقاً خواسته شما ("کمتر شدن") اجرا شود، فرض می‌کنم شما در گیت‌هاب دو متغیر دارید:
    # GOLD_PRICE_HIGH و GOLD_PRICE_LOW. اگر ندارید، کد زیر به صورت هوشمند عمل می‌کند:
    
    high_threshold_gold = float(os.getenv('GOLD_PRICE_HIGH', GOLD_PRICE_THRESHOLD))
    low_threshold_gold = float(os.getenv('GOLD_PRICE_LOW', GOLD_PRICE_THRESHOLD * 0.95)) # پیش‌فرض 5% کمتر
    
    if gold_price_toman >= high_threshold_gold:
        msg = f"🔺 **هشدار طلا (سقف)**: قیمت به {gold_price_toman:,.0f} رسید (بیشتر از {high_threshold_gold:,.0f})"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "gold", "message": msg})
    
    if gold_price_toman <= low_threshold_gold:
        msg = f"🔻 **هشدار طلا (کف)**: قیمت به {gold_price_toman:,.0f} رسید (کمتر از {low_threshold_gold:,.0f})"
        send_telegram_alert(msg)
        alerts.append({"type": "price_low", "asset": "gold", "message": msg})

    # همین منطق برای نقره
    high_threshold_silver = float(os.getenv('SILVER_PRICE_HIGH', SILVER_PRICE_THRESHOLD))
    low_threshold_silver = float(os.getenv('SILVER_PRICE_LOW', SILVER_PRICE_THRESHOLD * 0.95))

    if silver_price_toman >= high_threshold_silver:
        msg = f"🔺 **هشدار نقره (سقف)**: قیمت به {silver_price_toman:,.0f} رسید"
        send_telegram_alert(msg)
        alerts.append({"type": "price_high", "asset": "silver", "message": msg})
    
    if silver_price_toman <= low_threshold_silver:
        msg = f"🔻 **هشدار نقره (کف)**: قیمت به {silver_price_toman:,.0f} رسید"
        send_telegram_alert(msg)
        alerts.append({"type": "price_low", "asset": "silver", "message": msg})

    # 3. محاسبات پرتفو
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
        
        print(f"📊 Portfolio NPL: {total_npl_percent:.2f}%")

        # 4. هشدارهای درصد سود/زیان
        if total_npl_percent >= PORTFOLIO_PROFIT_THRESHOLD:
            msg = f"🎉 **هشدار سود پرتفو**: سود به **{total_npl_percent:.2f}%** رسید."
            send_telegram_alert(msg)
            alerts.append({"type": "profit_target", "message": msg})
        
        elif total_npl_percent <= PORTFOLIO_LOSS_THRESHOLD:
            msg = f"📉 **هشدار زیان پرتفو**: زیان به **{total_npl_percent:.2f}%** رسید."
            send_telegram_alert(msg)
            alerts.append({"type": "loss_limit", "message": msg})
            
    else:
        print("⚠️ Portfolio inputs missing.")
        portfolio_summary = {"error": "Missing inputs"}

    # 5. خروجی نهایی
    final_payload = {
        "last_updated_fa": tehran_time_str,
        "last_updated_iso": timestamp,
        "market_status": "open",
        "assets_summary": {
            "gold": {"price_toman": round(gold_price_toman, 2), "daily_change_percent": round(gold_change, 2)},
            "silver": {"price_toman": round(silver_price_toman, 2), "daily_change_percent": round(silver_change, 2)}
        },
        "portfolio": portfolio_summary,
        "alerts": alerts
    }

    # ذخیره در Redis
    if redis_client:
        try:
            redis_client.set("latest_market_data", json.dumps(final_payload))
            history_item = {"time": timestamp, "gold": gold_price_toman, "silver": silver_price_toman, "npl": portfolio_summary.get("total_npl_percent", 0)}
            redis_client.lpush("market_history", json.dumps(history_item))
            redis_client.ltrim("market_history", 0, 99)
            print("💾 Data saved.")
        except Exception as e:
            print(f"❌ Redis Error: {e}")

    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_payload, f, ensure_ascii=False, indent=2)
    
    print("✅ Execution completed.")

if __name__ == "__main__":
    main()