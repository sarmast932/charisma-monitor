import os
import json
import requests
from datetime import datetime
from upstash_redis import Redis

# --- 1. پیکربندی و پاک‌سازی ورودی‌ها ---
# دریافت و تمیز کردن متغیرهای محیطی (حذف فاصله‌های اضافی)
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
CHAT_ID = os.getenv('CHAT_ID', '').strip()
UPSTASH_URL = os.getenv('UPSTASH_URL', '').strip()
UPSTASH_TOKEN = os.getenv('UPSTASH_TOKEN', '').strip()

# آستانه‌های هشدار (تومان)
GOLD_THRESHOLD = 3500000
SILVER_THRESHOLD = 45000

# اطلاعات پرتفوی (قابل تغییر)
PORTFOLIO = {
    "gold_buy_avg": 3200000,
    "gold_qty": 10,
    "silver_buy_avg": 40000,
    "silver_qty": 100
}

# اتصال به Upstash Redis
redis_client = None
try:
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise ValueError("Upstash credentials missing")
    redis_client = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
    print("✅ Connected to Upstash Redis")
except Exception as e:
    print(f"❌ Redis Connection Failed: {e}")

# --- 2. توابع کمکی ---

def send_telegram_alert(message):
    """ارسال پیام به تلگرام"""
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("📩 Alert sent to Telegram.")
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

def fetch_price_from_charisma(asset_name):
    """
    استخراج قیمت واقعی از API کاریزما با هندلینگ ساختار تو در تو
    الگو گرفته از نیاز به ورود به کلید 'data'
    """
    url = f"https://inv.charisma.ir/pub/Plans/{asset_name}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://inv.charisma.ir/'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        raw_json = response.json()
        
        # لاگ ساختار برای دیباگ (فقط کلیدهای سطح اول)
        print(f"📥 [{asset_name}] Raw Keys: {list(raw_json.keys())}")
        
        # گام حیاتی: ورود به آبجکت داخلی 'data' اگر وجود داشته باشد
        data_payload = raw_json
        if isinstance(raw_json, dict) and 'data' in raw_json:
            data_payload = raw_json['data']
            print(f"🔍 Navigated into 'data'. Inner Keys: {list(data_payload.keys())}")
        
        # لیست کلیدهای احتمالی قیمت در APIهای مالی
        price_keys = ['currentPrice', 'price', 'lastPrice', 'askPrice', 'value', 'nav']
        
        extracted_price_rial = 0
        
        if isinstance(data_payload, dict):
            # تلاش 1: جستجوی مستقیم کلیدهای شناخته شده
            for key in price_keys:
                if key in data_payload:
                    val = data_payload[key]
                    if isinstance(val, (int, float)) and val > 1000: # فیلتر اعداد معقول
                        extracted_price_rial = float(val)
                        print(f"✅ Found price via key '{key}': {extracted_price_rial}")
                        break
            
            # تلاش 2: اگر پیدا نشد، جستجو در تمام مقادیر عددی بزرگ
            if extracted_price_rial == 0:
                for k, v in data_payload.items():
                    if isinstance(v, (int, float)) and v > 100000: # اعداد بزرگتر از 100 هزار
                        # اطمینان از اینکه کلید مربوط به ID نباشد
                        if 'id' not in k.lower() and 'code' not in k.lower():
                            extracted_price_rial = float(v)
                            print(f"⚠️ Guessed price via key '{k}': {extracted_price_rial}")
                            break
            
            # تلاش 3: اگر باز هم نشد، چاپ کامل JSON برای بررسی دستی
            if extracted_price_rial == 0:
                print(f"❌ CRITICAL: No price found in JSON for {asset_name}")
                print(f"Full JSON Content: {json.dumps(data_payload, indent=2)}")
                return None
        
        return extracted_price_rial

    except Exception as e:
        print(f"❌ Error fetching {asset_name}: {e}")
        return None

def calculate_stats(current_price, buy_avg, qty):
    total_value = current_price * qty
    total_cost = buy_avg * qty
    net_profit = (total_value - total_cost) - (total_value * 0.01) # کسر 1% کارمزد
    percent = (net_profit / total_cost) * 100 if total_cost > 0 else 0
    return {
        "total_value": round(total_value, 2),
        "net_profit": round(net_profit, 2),
        "profit_percent": round(percent, 2)
    }

# --- 3. منطق اصلی (Main Execution) ---

def main():
    print("🚀 Starting Charisma Metals Monitor...")
    timestamp = datetime.now().isoformat()
    
    use_cache = False
    gold_toman = 0
    silver_toman = 0

    # دریافت قیمت‌ها
    gold_rial = fetch_price_from_charisma("Gold")
    silver_rial = fetch_price_from_charisma("Silver")

    # مدیریت خطا: اگر قیمت گرفته نشد، سعی کن از کش ردیس بخوانی
    if not gold_rial or not silver_rial:
        print("⚠️ Live fetch failed. Attempting to load from Redis cache...")
        if redis_client:
            cached_data = redis_client.get("latest_market_data")
            if cached_data:
                data_obj = json.loads(cached_data)
                gold_toman = data_obj['assets']['gold']['price_toman']
                silver_toman = data_obj['assets']['silver']['price_toman']
                use_cache = True
                print("✅ Successfully loaded cached data.")
            else:
                print("❌ No cache available. Exiting.")
                return
        else:
            print("❌ Redis not available. Exiting.")
            return
    
    if not use_cache:
        # تبدیل ریال به تومان
        # نکته: اگر API کاریزما قیمت را مستقیماً به تومان می‌دهد، تقسیم بر 10 را حذف کنید.
        # اما معمولاً APIهای ریالی هستند.
        # ضریب 0.75 برای طلای 18 عیار طبق فرمول شما
        gold_toman = (gold_rial / 10.0) * 0.75
        silver_toman = silver_rial / 10.0
        
        print(f"💰 [LIVE] Gold: {gold_toman:,.0f} Toman | Silver: {silver_toman:,.0f} Toman")
    else:
        print(f"💰 [CACHE] Gold: {gold_toman:,.0f} Toman | Silver: {silver_toman:,.0f} Toman")

    # محاسبات پرتفو
    gold_stats = calculate_stats(gold_toman, PORTFOLIO["gold_buy_avg"], PORTFOLIO["gold_qty"])
    silver_stats = calculate_stats(silver_toman, PORTFOLIO["silver_buy_avg"], PORTFOLIO["silver_qty"])
    
    total_val = gold_stats["total_value"] + silver_stats["total_value"]
    total_profit = gold_stats["net_profit"] + silver_stats["net_profit"]
    total_invest = (PORTFOLIO["gold_buy_avg"] * PORTFOLIO["gold_qty"]) + (PORTFOLIO["silver_buy_avg"] * PORTFOLIO["silver_qty"])
    total_percent = (total_profit / total_invest) * 100 if total_invest > 0 else 0

    # بررسی هشدارها (فقط در حالت زنده ارسال شود تا اسپم نشود)
    alerts = []
    if not use_cache:
        if gold_toman >= GOLD_THRESHOLD:
            msg = f"🔔 **هشدار طلا**: قیمت به {gold_toman:,.0f} تومان رسید."
            send_telegram_alert(msg)
            alerts.append({"asset": "gold", "message": msg})
        if silver_toman >= SILVER_THRESHOLD:
            msg = f"🔔 **هشدار نقره**: قیمت به {silver_toman:,.0f} تومان رسید."
            send_telegram_alert(msg)
            alerts.append({"asset": "silver", "message": msg})

    # ساخت آبجکت نهایی داده
    final_payload = {
        "last_updated": timestamp,
        "source": "cached" if use_cache else "live",
        "assets": {
            "gold": {"price_toman": round(gold_toman, 2)},
            "silver": {"price_toman": round(silver_toman, 2)}
        },
        "portfolio": {
            "total_value": round(total_val, 2),
            "net_profit_percent": round(total_percent, 2),
            "details": {"gold": gold_stats, "silver": silver_stats}
        },
        "alerts": alerts
    }

    # ذخیره در Redis
    if redis_client:
        try:
            redis_client.set("latest_market_data", json.dumps(final_payload))
            if not use_cache:
                # افزودن به تاریخچه (نگهداری 50 تای آخر)
                redis_client.lpush("market_history", json.dumps({"time": timestamp, "gold": gold_toman, "silver": silver_toman}))
                redis_client.ltrim("market_history", 0, 49)
            print("💾 Data saved to Redis.")
        except Exception as e:
            print(f"❌ Redis Save Error: {e}")

    # تولید فایل JSON برای GitHub Pages
    try:
        with open("market_data.json", "w", encoding="utf-8") as f:
            json.dump(final_payload, f, ensure_ascii=False, indent=2)
        print("📄 market_data.json generated successfully.")
    except Exception as e:
        print(f"❌ File Write Error: {e}")

    print("✅ Execution completed successfully.")

if __name__ == "__main__":
    main()