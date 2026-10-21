# price_tracker_v2.py - Amazon India price tracker (read-only, no login, no buy).
# Same as v1 except the fetch layer: pages come from the GoLogin Web Unlocker API.
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

load_dotenv()  # reads GOLOGIN_WEBUNLOCKER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = (os.getenv("GOLOGIN_WEBUNLOCKER_API_KEY") or "").strip()  # v2 only

PRODUCTS_FILE = "products.json"
DB_FILE = "prices_v2.db"  # separate file so v1 and v2 histories never mix
TIMEOUT = 60  # seconds. The API renders the page first, so give it more time.
# The Web Unlocker does the IP rotation / browser work for us, so no UA here.
API_URL = "https://parsing.webunlocker.gologin.com/v1/scrape"
MAX_ATTEMPTS = 3  # 1 try + 2 retries
RETRY_STATUSES = (408, 429, 500, 502, 503, 504)  # only these are worth retrying
FREE_TRIAL_REQUESTS = 1000  # free trial size, just for the log line

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def fetch_page(url):
    """Fetch one page through the GoLogin Web Unlocker API instead of directly.

    Same return value as v1: a requests.Response holding the HTML, or None.
    Retries only on network errors and RETRY_STATUSES, with exponential backoff.
    """
    if not API_KEY:
        logger.error("GOLOGIN_WEBUNLOCKER_API_KEY is not set (.env or GitHub secret).")
        return None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:  # requests URL-encodes the `url` param for us
            resp = requests.get(API_URL, params={"url": url}, headers={"apikey": API_KEY}, timeout=TIMEOUT)
        except requests.RequestException as e:
            logger.warning(f"Network error (attempt {attempt}/{MAX_ATTEMPTS}) for {url}: {e}")
            if attempt == MAX_ATTEMPTS:
                logger.warning(f"BLOCKED: {url} | fetch failed after {MAX_ATTEMPTS} attempts.")
                return None
            backoff(attempt)
            continue
        if resp.status_code == 200:  # only successful pages are billed
            used = count_request()
            logger.info(f"Web Unlocker requests used: {used} / {FREE_TRIAL_REQUESTS}")
            return resp
        if resp.status_code in RETRY_STATUSES:  # 408/429/500/502/503/504
            logger.warning(f"API returned {resp.status_code} (attempt {attempt}/{MAX_ATTEMPTS}) for {url}.")
            if attempt == MAX_ATTEMPTS:
                logger.warning(f"BLOCKED: {url} | still {resp.status_code} after {MAX_ATTEMPTS} attempts.")
                return None
            backoff(attempt)
            continue
        if resp.status_code in (401, 403):  # bad key - retrying cannot help
            logger.error(f"API key rejected (HTTP {resp.status_code}). Check GOLOGIN_WEBUNLOCKER_API_KEY.")
        elif resp.status_code == 422:
            logger.error(f"API rejected the url (HTTP 422): {url}. Use a full https://... link.")
        else:
            logger.error(f"API returned unexpected HTTP {resp.status_code} for {url}.")
        return None  # no retry for these

def backoff(attempt):
    """Wait before a retry: ~2s, then ~4s (plus jitter so runs don't sync up)."""
    delay = 2 ** attempt + random.uniform(0, 1)
    logger.info(f"Retrying in {delay:.1f}s...")
    time.sleep(delay)

def count_request():
    """Count one billable (HTTP 200) request for this month, return the total."""
    month = datetime.now().strftime("%Y-%m")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO api_usage (month, requests) VALUES (?,1) ON CONFLICT(month) DO UPDATE SET requests = requests + 1", (month,))
    conn.commit()
    total = conn.execute("SELECT requests FROM api_usage WHERE month=?", (month,)).fetchone()[0]
    conn.close()
    return total

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
    """Create prices_v2.db tables (prices + the monthly API counter) if needed."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, title TEXT, price REAL, checked_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS api_usage (month TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0)")  # v2 only
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
            continue  # retries already happened in fetch_page(), move on
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
