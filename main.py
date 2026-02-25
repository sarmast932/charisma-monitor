import os
import json
import requests
from datetime import datetime
from upstash_redis import Redis

# --- 1. پیکربندی و اتصال به دیتابیس ---
# دریافت متغیرها از GitHub Secrets
bot_token = os.getenv('BOT_TOKEN')
chat_id = os.getenv('CHAT_ID')
upstash_url = os.getenv('UPSTASH_URL')
upstash_token = os.getenv('UPSTASH_TOKEN')

# تنظیمات آستانه قیمت (می‌توانید این اعداد را تغییر دهید)
GOLD_THRESHOLD = 3500000  # مثال: 3,500,000 تومان
SILVER_THRESHOLD = 45000  # مثال: 45,000 تومان

# اطلاعات پرتفوی نمونه (قابل تغییر)
PORTFOLIO = {
    "gold_buy_avg": 3200000,
    "gold_qty": 10,
    "silver_buy_avg": 40000,
    "silver_qty": 100
}

# اتصال به Upstash Redis
try:
    redis = Redis(url=upstash_url, token=upstash_token)
    print("✅ Connected to Upstash Redis")
except Exception as e:
    print(f"❌ Redis Connection Failed: {e}")
    redis = None

# --- 2. توابع کمکی ---

def send_telegram_alert(message):
    """ارسال پیام هشدار به تلگرام"""
    if not bot_token or not chat_id:
        print("⚠️ Telegram credentials missing.")
        return
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("📩 Alert sent to Telegram.")
        else:
            print(f"⚠️ Telegram API Error: {response.text}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def fetch_charisma_price(plan_type):
    """دریافت قیمت از API کاریزما"""
    url = f"https://inv.charisma.ir/pub/Plans/{plan_type}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # استخراج هوشمند قیمت از ساختار JSON
        price_rial = 0
        if isinstance(data, dict):
            # جستجو در کلیدهای احتمالی
            for key in ['Price', 'LastPrice', 'Value', 'CurrentPrice']:
                if key in data and isinstance(data[key], (int, float)):
                    price_rial = float(data[key])
                    break
            # اگر کلید مستقیم نبود، اولین مقدار عددی بزرگ را بردار
            if price_rial == 0:
                for val in data.values():
                    if isinstance(val, (int, float)) and val > 1000:
                        price_rial = float(val)
                        break
        elif isinstance(data, list) and len(data) > 0:
            item = data[0]
            if isinstance(item, dict):
                for key in ['Price', 'LastPrice', 'Value']:
                    if key in item and isinstance(item[key], (int, float)):
                        price_rial = float(item[key])
                        break
        
        return price_rial
    except Exception as e:
        print(f"❌ Error fetching {plan_type}: {e}")
        return None

def calculate_portfolio_stats(current_price, buy_avg, qty):
    """محاسبه سود و زیان"""
    total_value = current_price * qty
    total_cost = buy_avg * qty
    gross_profit = total_value - total_cost
    fee = total_value * 0.01  # کارمزد 1 درصدی فرضی
    net_profit = gross_profit - fee
    profit_percent = (net_profit / total_cost) * 100 if total_cost > 0 else 0
    return {
        "total_value": round(total_value, 2),
        "net_profit": round(net_profit, 2),
        "profit_percent": round(profit_percent, 2)
    }

# --- 3. منطق اصلی برنامه ---

def main():
    print("🚀 Starting Charisma Metals Monitor...")
    timestamp = datetime.now().isoformat()

    # الف) دریافت قیمت‌ها
    gold_price_rial = fetch_charisma_price("Gold")
    silver_price_rial = fetch_charisma_price("Silver")

    if not gold_price_rial or not silver_price_rial:
        print("⛔ Failed to fetch prices. Exiting.")
        return

    # ب) تبدیل واحد (ریال به تومان و اعمال ضرایب)
    # طلا: تقسیم بر 10 برای تومان، ضرب در 0.75 برای عیار 18
    gold_price_toman = (gold_price_rial / 10) * 0.75
    # نقره: تقسیم بر 10 برای تومان
    silver_price_toman = silver_price_rial / 10

    print(f"💰 Gold: {gold_price_toman:,.0f} Toman | Silver: {silver_price_toman:,.0f} Toman")

    # ج) محاسبات پرتفو
    gold_stats = calculate_portfolio_stats(gold_price_toman, PORTFOLIO["gold_buy_avg"], PORTFOLIO["gold_qty"])
    silver_stats = calculate_portfolio_stats(silver_price_toman, PORTFOLIO["silver_buy_avg"], PORTFOLIO["silver_qty"])
    
    total_portfolio_value = gold_stats["total_value"] + silver_stats["total_value"]
    total_net_profit = gold_stats["net_profit"] + silver_stats["net_profit"]
    total_investment = (PORTFOLIO["gold_buy_avg"] * PORTFOLIO["gold_qty"]) + (PORTFOLIO["silver_buy_avg"] * PORTFOLIO["silver_qty"])
    total_profit_percent = (total_net_profit / total_investment) * 100 if total_investment > 0 else 0

    # د) بررسی شرایط هشدار
    alerts = []
    if gold_price_toman >= GOLD_THRESHOLD:
        msg = f"🔔 **هشدار طلا**\nقیمت فعلی: **{gold_price_toman:,.0f}** تومان\nاز مرز {GOLD_THRESHOLD:,.0f} عبور کرد!"
        send_telegram_alert(msg)
        alerts.append({"asset": "gold", "message": msg})
    
    if silver_price_toman >= SILVER_THRESHOLD:
        msg = f"🔔 **هشدار نقره**\nقیمت فعلی: **{silver_price_toman:,.0f}** تومان\nاز مرز {SILVER_THRESHOLD:,.0f} عبور کرد!"
        send_telegram_alert(msg)
        alerts.append({"asset": "silver", "message": msg})

    # هـ) آماده‌سازی داده نهایی
    final_data = {
        "last_updated": timestamp,
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

    # و) ذخیره‌سازی در Upstash Redis
    if redis:
        try:
            redis.set("latest_market_data", json.dumps(final_data))
            # ذخیره تاریخچه (نگهداری 50 رکورد آخر)
            redis.lpush("market_history", json.dumps({"time": timestamp, "gold": gold_price_toman, "silver": silver_price_toman}))
            redis.ltrim("market_history", 0, 49)
            print("💾 Data saved to Redis.")
        except Exception as e:
            print(f"❌ Redis Save Error: {e}")

    # ز) تولید فایل JSON برای GitHub Pages
    try:
        with open("market_data.json", "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        print("📄 market_data.json generated successfully.")
    except Exception as e:
        print(f"❌ JSON File Error: {e}")

    print("✅ Execution completed.")

if __name__ == "__main__":
    main()