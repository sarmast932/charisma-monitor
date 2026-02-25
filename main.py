import os
import json
import requests
from datetime import datetime

# --- پیکربندی (Configuration) ---
# دریافت متغیرهای محیطی از GitHub Secrets
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# دریافت آستانه قیمت‌ها از GitHub Variables (با مقدار پیش‌فرض ایمن)
try:
    GOLD_THRESHOLD = float(os.getenv('GOLD_THRESHOLD', 1500000))
    SILVER_THRESHOLD = float(os.getenv('SILVER_THRESHOLD', 20000))
except ValueError:
    GOLD_THRESHOLD = 1500000
    SILVER_THRESHOLD = 20000

# تنظیمات پرتفو (مقادیر نمونه - قابل ویرایش مستقیم یا انتقال به Secrets)
PORTFOLIO = {
    "gold_buy_avg": 1400000,  # قیمت میانگین خرید طلا (تومان)
    "gold_qty": 10,           # تعداد واحد طلا
    "silver_buy_avg": 19000,  # قیمت میانگین خرید نقره (تومان)
    "silver_qty": 100         # تعداد واحد نقره
}

# آدرس APIهای رسمی کاریزما
API_URL_GOLD = "https://inv.charisma.ir/pub/Plans/Gold"
API_URL_SILVER = "https://inv.charisma.ir/pub/Plans/Silver"

# نام فایل خروجی
OUTPUT_FILE = "market_data.json"

def send_telegram_message(message):
    """ارسال پیام به تلگرام"""
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ تنظیمات تلگرام یافت نشد. ارسال پیام لغو شد.")
        return
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ هشدار با موفقیت به تلگرام ارسال شد.")
        else:
            print(f"❌ خطا در ارسال تلگرام: {response.text}")
    except Exception as e:
        print(f"❌ خطای ارتباطی با تلگرام: {e}")

def fetch_price(url, asset_name):
    """دریافت قیمت از API و استخراج عدد قیمت با منطق انعطاف‌پذیر"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        price_rial = 0
        
        if isinstance(data, dict):
            possible_keys = ['Price', 'LastPrice', 'Value', 'CurrentPrice', 'price', 'value']
            for key in possible_keys:
                if key in data:
                    price_rial = float(data[key])
                    break
            if price_rial == 0:
                for value in data.values():
                    if isinstance(value, (int, float)) and value > 1000:
                        price_rial = float(value)
                        break
                        
        elif isinstance(data, list) and len(data) > 0:
            item = data[0]
            if isinstance(item, dict):
                possible_keys = ['Price', 'LastPrice', 'Value', 'CurrentPrice']
                for key in possible_keys:
                    if key in item:
                        price_rial = float(item[key])
                        break
        
        if price_rial == 0:
            raise ValueError(f"ساختار JSON ناشناخته برای {asset_name}. داده خام: {data}")
            
        return price_rial

    except Exception as e:
        print(f"❌ خطا در دریافت قیمت {asset_name}: {e}")
        return None

def calculate_profit(current_price_toman, buy_avg, qty, fee_percent):
    """محاسبه سود خالص با کسر کارمزد فروش"""
    total_value = current_price_toman * qty
    total_cost = buy_avg * qty
    gross_profit = total_value - total_cost
    fee = (total_value * fee_percent) / 100.0
    net_profit = gross_profit - fee
    profit_percent = (net_profit / total_cost) * 100.0 if total_cost > 0 else 0.0
    
    return {
        "total_value": round(total_value, 2),
        "net_profit": round(net_profit, 2),
        "profit_percent": round(profit_percent, 2)
    }

def main():
    print(f"🚀 شروع اجرای Charisma Monitor در ساعت {datetime.now().strftime('%H:%M:%S')}")
    
    # 1. دریافت قیمت‌ها
    gold_price_rial = fetch_price(API_URL_GOLD, "Gold")
    silver_price_rial = fetch_price(API_URL_SILVER, "Silver")
    
    if gold_price_rial is None or silver_price_rial is None:
        print("⛔ دریافت قیمت ناموفق بود. اجرا متوقف شد.")
        return

    # 2. تبدیل واحدها و اعمال ضرایب
    gold_price_toman = (gold_price_rial / 10.0) * 0.75
    silver_price_toman = silver_price_rial / 10.0
    
    print(f"💰 قیمت طلا: {gold_price_toman:,.0f} تومان")
    print(f"💰 قیمت نقره: {silver_price_toman:,.0f} تومان")

    # 3. محاسبات پرتفو
    gold_stats = calculate_profit(gold_price_toman, PORTFOLIO['gold_buy_avg'], PORTFOLIO['gold_qty'], 1.0)
    silver_stats = calculate_profit(silver_price_toman, PORTFOLIO['silver_buy_avg'], PORTFOLIO['silver_qty'], 1.0)
    
    total_investment_cost = (PORTFOLIO['gold_buy_avg'] * PORTFOLIO['gold_qty']) + \
                            (PORTFOLIO['silver_buy_avg'] * PORTFOLIO['silver_qty'])
                            
    total_portfolio_value = gold_stats['total_value'] + silver_stats['total_value']
    total_net_profit = gold_stats['net_profit'] + silver_stats['net_profit']
    total_profit_percent = (total_net_profit / total_investment_cost) * 100.0 if total_investment_cost > 0 else 0.0

    # 4. بررسی شرایط هشدار
    alerts = []
    
    if gold_price_toman >= GOLD_THRESHOLD:
        msg = f"🔔 **هشدار قیمت طلا**\n\nقیمت فعلی: **{gold_price_toman:,.0f}** تومان\nاز مرز هشدار ({GOLD_THRESHOLD:,.0f}) عبور کرد!\n\nسود پرتفو طلا: {gold_stats['profit_percent']:.2f}%"
        send_telegram_message(msg)
        alerts.append({"type": "gold_high", "message": f"طلا از مرز {GOLD_THRESHOLD:,.0f} عبور کرد.", "timestamp": datetime.now().isoformat()})
        
    if silver_price_toman >= SILVER_THRESHOLD:
        msg = f"🔔 **هشدار قیمت نقره**\n\nقیمت فعلی: **{silver_price_toman:,.0f}** تومان\nاز مرز هشدار ({SILVER_THRESHOLD:,.0f}) عبور کرد!\n\nسود پرتفو نقره: {silver_stats['profit_percent']:.2f}%"
        send_telegram_message(msg)
        alerts.append({"type": "silver_high", "message": f"نقره از مرز {SILVER_THRESHOLD:,.0f} عبور کرد.", "timestamp": datetime.now().isoformat()})

    # 5. آماده‌سازی داده‌ها برای خروجی JSON
    output_data = {
        "project_name": "Charisma Investment",
        "last_updated": datetime.now().isoformat(),
        "market_status": "open",
        "assets": {
            "gold": {
                "price_toman": round(gold_price_toman, 2),
                "price_rial_raw": gold_price_rial,
                "factor_applied": 0.75,
                "trend": "neutral"
            },
            "silver": {
                "price_toman": round(silver_price_toman, 2),
                "price_rial_raw": silver_price_rial,
                "factor_applied": 1.0,
                "trend": "neutral"
            }
        },
        "portfolio": {
            "total_value": round(total_portfolio_value, 2),
            "total_investment": round(total_investment_cost, 2),
            "net_profit_amount": round(total_net_profit, 2),
            "net_profit_percent": round(total_profit_percent, 2),
            "details": {
                "gold": gold_stats,
                "silver": silver_stats
            }
        },
        "alerts": alerts
    }

    # 6. نوشتن در فایل JSON
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"✅ داده‌ها با موفقیت در {OUTPUT_FILE} ذخیره شدند.")
    except Exception as e:
        print(f"❌ خطا در ذخیره فایل JSON: {e}")

if __name__ == "__main__":
    main()