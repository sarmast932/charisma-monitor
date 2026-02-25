import os
import json
import requests
from datetime import datetime
from upstash_redis import Redis

# --- 1. پیکربندی ---
bot_token = os.getenv('BOT_TOKEN')
chat_id = os.getenv('CHAT_ID')
upstash_url = os.getenv('UPSTASH_URL')
upstash_token = os.getenv('UPSTASH_TOKEN')

GOLD_THRESHOLD = 3500000
SILVER_THRESHOLD = 45000

PORTFOLIO = {
    "gold_buy_avg": 3200000,
    "gold_qty": 10,
    "silver_buy_avg": 40000,
    "silver_qty": 100
}

# اتصال به Redis
try:
    redis = Redis(url=upstash_url, token=upstash_token)
    print("✅ Connected to Upstash Redis")
except Exception as e:
    print(f"❌ Redis Connection Failed: {e}")
    redis = None

# --- 2. توابع کمکی با منطق قوی‌تر ---

def send_telegram_alert(message):
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def fetch_charisma_price(plan_type):
    """دریافت قیمت با هدرهای پیشرفته برای دور زدن محدودیت‌ها"""
    url = f"https://inv.charisma.ir/pub/Plans/{plan_type}"
    
    # هدرهایی که درخواست را شبیه مرورگر واقعی می‌کند
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8',
        'Referer': 'https://inv.charisma.ir/',
        'Connection': 'keep-alive'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        print(f"📥 Raw Data for {plan_type}: {str(data)[:200]}...") # چاپ بخشی از داده برای دیباگ
        
        price_rial = 0
        
        # استراتژی 1: جستجوی کلیدهای معروف
        if isinstance(data, dict):
            keys_to_check = ['Price', 'LastPrice', 'Value', 'CurrentPrice', 'price', 'value', 'lastPrice']
            for key in keys_to_check:
                if key in data:
                    val = data[key]
                    if isinstance(val, (int, float)):
                        price_rial = float(val)
                        break
            
            # استراتژی 2: اگر کلید مستقیم نبود، گشتن در مقادیر
            if price_rial == 0:
                for key, val in data.items():
                    if isinstance(val, (int, float)) and val > 1000: # فرض بر اینکه قیمت عدد بزرگی است
                        price_rial = float(val)
                        break
            
            # استراتژی 3: بررسی آبجکت‌های تو در تو
            if price_rial == 0:
                for key, val in data.items():
                    if isinstance(val, dict):
                        for sub_key, sub_val in val.items():
                            if isinstance(sub_val, (int, float)) and sub_val > 1000:
                                price_rial = float(sub_val)
                                break
                    if price_rial > 0: break

        elif isinstance(data, list) and len(data) > 0:
            item = data[0]
            if isinstance(item, dict):
                keys_to_check = ['Price', 'LastPrice', 'Value', 'CurrentPrice']
                for key in keys_to_check:
                    if key in item and isinstance(item[key], (int, float)):
                        price_rial = float(item[key])
                        break
        
        if price_rial == 0:
            print(f"⚠️ Could not extract price from JSON for {plan_type}")
            return None
            
        return price_rial

    except Exception as e:
        print(f"❌ Error fetching {plan_type}: {e}")
        return None

def calculate_portfolio_stats(current_price, buy_avg, qty):
    total_value = current_price * qty
    total_cost = buy_avg * qty
    gross_profit = total_value - total_cost
    fee = total_value * 0.01
    net_profit = gross_profit - fee
    profit_percent = (net_profit / total_cost) * 100 if total_cost > 0 else 0
    return {
        "total_value": round(total_value, 2),
        "net_profit": round(net_profit, 2),
        "profit_percent": round(profit_percent, 2)
    }

# --- 3. منطق اصلی ---

def main():
    print("🚀 Starting Charisma Metals Monitor...")
    timestamp = datetime.now().isoformat()

    gold_price_rial = fetch_charisma_price("Gold")
    silver_price_rial = fetch_charisma_price("Silver")

    # اگر قیمت‌ها گرفته نشدند، سعی می‌کنیم از ردیس بخوانیم تا سایت خالی نماند
    use_cached = False
    if not gold_price_rial or not silver_price_rial:
        print("⚠️ Failed to fetch live prices. Attempting to use cached data from Redis...")
        if redis:
            try:
                cached_data = redis.get("latest_market_data")
                if cached_data:
                    print("✅ Using cached data.")
                    data_obj = json.loads(cached_data)
                    gold_price_toman = data_obj['assets']['gold']['price_toman']
                    silver_price_toman = data_obj['assets']['silver']['price_toman']
                    use_cached = True
                else:
                    print("❌ No cached data found. Exiting.")
                    return
            except:
                print("❌ Failed to retrieve cache. Exiting.")
                return
        else:
            print("❌ Redis not available. Exiting.")
            return

    if not use_cached:
        # محاسبات اگر داده جدید گرفتیم
        gold_price_toman = (gold_price_rial / 10) * 0.75
        silver_price_toman = silver_price_rial / 10
        print(f"💰 Live Gold: {gold_price_toman:,.0f} | Silver: {silver_price_toman:,.0f}")
    else:
        print(f"💰 Cached Gold: {gold_price_toman:,.0f} | Silver: {silver_price_toman:,.0f}")

    # محاسبات پرتفو
    gold_stats = calculate_portfolio_stats(gold_price_toman, PORTFOLIO["gold_buy_avg"], PORTFOLIO["gold_qty"])
    silver_stats = calculate_portfolio_stats(silver_price_toman, PORTFOLIO["silver_buy_avg"], PORTFOLIO["silver_qty"])
    
    total_portfolio_value = gold_stats["total_value"] + silver_stats["total_value"]
    total_net_profit = gold_stats["net_profit"] + silver_stats["net_profit"]
    total_investment = (PORTFOLIO["gold_buy_avg"] * PORTFOLIO["gold_qty"]) + (PORTFOLIO["silver_buy_avg"] * PORTFOLIO["silver_qty"])
    total_profit_percent = (total_net_profit / total_investment) * 100 if total_investment > 0 else 0

    # بررسی هشدار (فقط اگر داده زنده باشد)
    alerts = []
    if not use_cached:
        if gold_price_toman >= GOLD_THRESHOLD:
            msg = f"🔔 **هشدار طلا**: {gold_price_toman:,.0f} تومان"
            send_telegram_alert(msg)
            alerts.append({"asset": "gold", "message": msg})
        if silver_price_toman >= SILVER_THRESHOLD:
            msg = f"🔔 **هشدار نقره**: {silver_price_toman:,.0f} تومان"
            send_telegram_alert(msg)
            alerts.append({"asset": "silver", "message": msg})

    final_data = {
        "last_updated": timestamp,
        "source": "cached" if use_cached else "live",
        "assets": {
            "gold": {"price_toman": round(gold_price_toman, 2)},
            "silver": {"price_toman": round(silver_price_toman, 2)}
        },
        "portfolio": {
            "total_value": round(total_portfolio_value, 2),
            "net_profit_percent": round(total_profit_percent, 2),
            "details": {"gold": gold_stats, "silver": silver_stats}
        },
        "alerts": alerts
    }

    # ذخیره در ردیس (حتی اگر کش باشد، تایم‌استمپ آپدیت می‌شود)
    if redis:
        try:
            redis.set("latest_market_data", json.dumps(final_data))
            if not use_cached:
                redis.lpush("market_history", json.dumps({"time": timestamp, "gold": gold_price_toman, "silver": silver_price_toman}))
                redis.ltrim("market_history", 0, 49)
            print("💾 Data saved to Redis.")
        except Exception as e:
            print(f"❌ Redis Save Error: {e}")

    # تولید فایل JSON
    try:
        with open("market_data.json", "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        print("📄 market_data.json generated successfully.")
    except Exception as e:
        print(f"❌ JSON File Error: {e}")

    print("✅ Execution completed.")

if __name__ == "__main__":
    main()