import os
import json
import requests
from datetime import datetime
from upstash_redis import Redis

# --- 1. پیکربندی ---
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
CHAT_ID = os.getenv('CHAT_ID', '').strip()
UPSTASH_URL = os.getenv('UPSTASH_URL', '').strip()
UPSTASH_TOKEN = os.getenv('UPSTASH_TOKEN', '').strip()

GOLD_THRESHOLD = 3500000
SILVER_THRESHOLD = 45000

PORTFOLIO = {
    "gold_buy_avg": 3200000,
    "gold_qty": 10,
    "silver_buy_avg": 40000,
    "silver_qty": 100
}

# اتصال به Redis
redis_client = None
try:
    if UPSTASH_URL and UPSTASH_TOKEN:
        redis_client = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
        print("✅ Connected to Upstash Redis")
except Exception as e:
    print(f"❌ Redis Connection Failed: {e}")

# --- 2. توابع ---

def send_telegram_alert(message):
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def fetch_price_from_charisma(asset_name):
    """
    استخراج قیمت از فیلد دقیق: data.latestIndexPrice.index
    """
    url = f"https://inv.charisma.ir/pub/Plans/{asset_name}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://inv.charisma.ir/'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        raw_json = response.json()
        
        print(f"📥 [{asset_name}] API Response received")
        
        # ورود به آبجکت data
        if not isinstance(raw_json, dict) or 'data' not in raw_json:
            print(f"❌ No 'data' key in response for {asset_name}")
            return None
        
        data = raw_json['data']
        
        # استخراج قیمت از latestIndexPrice.index
        price_rial = 0
        
        if 'latestIndexPrice' in data and isinstance(data['latestIndexPrice'], dict):
            if 'index' in data['latestIndexPrice']:
                price_rial = float(data['latestIndexPrice']['index'])
                print(f"✅ Found price in latestIndexPrice.index: {price_rial}")
        
        # fallback: اگر latestIndexPrice نبود، prevIndexPrice را چک کن
        if price_rial == 0 and 'prevIndexPrice' in data and isinstance(data['prevIndexPrice'], dict):
            if 'index' in data['prevIndexPrice']:
                price_rial = float(data['prevIndexPrice']['index'])
                print(f"⚠️ Using prevIndexPrice.index: {price_rial}")
        
        if price_rial == 0:
            print(f"❌ CRITICAL: No price found in JSON for {asset_name}")
            print(f"Available keys in data: {list(data.keys())}")
            return None
        
        return price_rial

    except Exception as e:
        print(f"❌ Error fetching {asset_name}: {e}")
        return None

def calculate_stats(current_price, buy_avg, qty):
    total_value = current_price * qty
    total_cost = buy_avg * qty
    net_profit = (total_value - total_cost) - (total_value * 0.01)
    percent = (net_profit / total_cost) * 100 if total_cost > 0 else 0
    return {
        "total_value": round(total_value, 2),
        "net_profit": round(net_profit, 2),
        "profit_percent": round(percent, 2)
    }

# --- 3. منطق اصلی ---

def main():
    print("🚀 Starting Charisma Metals Monitor...")
    timestamp = datetime.now().isoformat()
    
    use_cache = False
    gold_toman = 0
    silver_toman = 0

    # دریافت قیمت‌ها
    gold_rial = fetch_price_from_charisma("Gold")
    silver_rial = fetch_price_from_charisma("Silver")

    # مدیریت خطا
    if not gold_rial or not silver_rial:
        print("⚠️ Live fetch failed. Trying cache...")
        if redis_client:
            cached = redis_client.get("latest_market_data")
            if cached:
                d = json.loads(cached)
                gold_toman = d['assets']['gold']['price_toman']
                silver_toman = d['assets']['silver']['price_toman']
                use_cache = True
                print("✅ Using cached data.")
            else:
                print("❌ No cache. Exiting.")
                return
        else:
            print("❌ No Redis. Exiting.")
            return
    
    if not use_cache:
        # تبدیل ریال به تومان (تقسیم بر 10)
        # ضریب 0.75 برای طلای 18 عیار (طبق فرمول شما)
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

    # هشدارها
    alerts = []
    if not use_cache:
        if gold_toman >= GOLD_THRESHOLD:
            msg = f"🔔 **هشدار طلا**: {gold_toman:,.0f} تومان"
            send_telegram_alert(msg)
            alerts.append({"asset": "gold", "message": msg})
        if silver_toman >= SILVER_THRESHOLD:
            msg = f"🔔 **هشدار نقره**: {silver_toman:,.0f} تومان"
            send_telegram_alert(msg)
            alerts.append({"asset": "silver", "message": msg})

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
                redis_client.lpush("market_history", json.dumps({"time": timestamp, "gold": gold_toman, "silver": silver_toman}))
                redis_client.ltrim("market_history", 0, 49)
            print("💾 Saved to Redis.")
        except Exception as e:
            print(f"❌ Redis Save Error: {e}")

    # تولید JSON
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_payload, f, ensure_ascii=False, indent=2)
    print("📄 market_data.json generated.")
    print("✅ Execution completed.")

if __name__ == "__main__":
    main()