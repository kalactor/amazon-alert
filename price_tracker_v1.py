# price_tracker_v1.py - Amazon India price tracker (read-only, no login, no buy).
# Run once per execution (cron / GitHub Actions). Expects products.json + .env.
# Needs: requests, beautifulsoup4 (lxml), python-dotenv. See requirements.txt.
import json
import logging
import os
import random
import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()  # reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PRODUCTS_FILE = "products.json"
DB_FILE = "prices.db"
TIMEOUT = 15  # seconds
# One plain desktop UA on purpose. No proxies/rotation (video shows it blocked).
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def fetch_page(url):
    """Fetch one page with plain requests.get(). Swap this fn later if needed."""
    try:
        return requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        logger.warning(f"BLOCKED: {url} | fetch failed: {e}")
        return None

def is_blocked(response, price):
    """True if Amazon blocked us: 403/503, captcha text, or no price."""
    if response is None:  # network error already logged in fetch_page()
        logger.warning("BLOCKED: no response (network error/timeout)")
        return True
    status = response.status_code
    if status in (403, 503):  # classic Amazon block codes
        logger.warning(f"BLOCKED: status {status}, got a captcha page for {response.url}")
        return True
    low = response.text.lower()  # look for captcha / robot-check pages
    for phrase in ("robot check", "captcha", "automated access", "enter the characters"):
        if phrase in low:
            logger.warning(f"BLOCKED: status {status}, got a captcha page ({phrase!r} found)")
            return True
    if price is None:  # page OK but price missing = likely blocked layout
        logger.warning(f"BLOCKED: status {status}, no price found (probably blocked)")
        return True
    return False

def clean_price(text):
    """Turn 'Rs. 1,299.00' / '₹1,299' into 1299.0, else None."""
    cleaned = re.sub(r"[^\d.]", "", text.replace("₹", "").replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None

def parse_product(html):
    """Return (title, price) from product HTML using lxml parser."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.select_one("#productTitle")  # product title
    title = tag.get_text(strip=True) if tag else "Unknown product"
    price = None  # try usual Amazon price spots in order
    for sel in ("span.a-price-whole", "span.a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice"):
        el = soup.select_one(sel)
        if el:
            price = clean_price(el.get_text())
            if price is not None:
                break
    return title, price

def init_db():
    """Create prices.db table (url, title, price, checked_at) if needed."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, title TEXT, price REAL, checked_at TEXT)")
    conn.commit()
    conn.close()

def get_last_price(url):
    """Last saved price for url, or None on first run."""
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT price FROM prices WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
    conn.close()
    return row[0] if row else None

def save_price(url, title, price):
    """Save every successful check."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO prices (url, title, price, checked_at) VALUES (?,?,?,?)", (url, title, price, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()

def send_telegram(message):
    """Send message via Bot API sendMessage."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured (.env missing), skipping alert.")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15)
        r.raise_for_status()
        logger.info("Telegram alert sent.")
    except requests.RequestException as e:
        logger.warning(f"Telegram send failed: {e}")

def main():
    """Check each product once, alert on target hit or >=5% drop."""
    init_db()
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        products = json.load(f)  # list of {"url":..., "target_price":...}
    for i, p in enumerate(products):
        url, target = p["url"], float(p["target_price"])
        logger.info(f"Checking: {url}")
        resp = fetch_page(url)
        if resp is None:
            continue  # no retry loop, move to next product
        title, price = parse_product(resp.text)
        if is_blocked(resp, price):
            continue  # logged inside is_blocked(), no crash, no retry
        logger.info(f"Found: {title[:60]}... at Rs.{price}")
        last = get_last_price(url)
        save_price(url, title, price)
        if last is not None and price == last:
            logger.info("Same as last check, no alert.")  # avoid duplicate alerts
        elif price <= target:
            send_telegram(f"🔻 {title}\nNow Rs.{price} (target Rs.{target})\n{url}")
        elif last and last > 0 and (last - price) / last >= 0.05:
            pct = (last - price) / last * 100
            send_telegram(f"🔻 {title}\nDropped {pct:.1f}%: Rs.{last} -> Rs.{price}\n{url}")
        else:
            logger.info("No alert condition met.")
        if i < len(products) - 1:
            time.sleep(random.uniform(3, 8))  # polite pause between products

if __name__ == "__main__":
    main()
