# Amazon India Price Tracker (v1 and v2)

A small, read-only price tracker. It reads the product pages listed in
`products.json`, stores every price in SQLite, and sends you a Telegram message
when a price hits your target or drops by 5% or more.

This repo contains **two versions of the same bot** side by side:

| | v1 | v2 |
|---|---|---|
| Script | `price_tracker_v1.py` | `price_tracker_v2.py` |
| Fetch | plain `requests.get()` + one User-Agent | GoLogin Web Unlocker API |
| Timeout | 15s | 60s (the API renders the page first) |
| Retries | none | 3 attempts, exponential backoff |
| Database | `prices.db` | `prices_v2.db` |
| From home IP | usually works | works |
| From GitHub Actions | usually **blocked** (datacenter IP) | works |

**Everything except the fetch layer is identical.** Parsing, the SQLite
storage, the block detection and the Telegram code are the same in both files.
Run `git diff --no-index price_tracker_v1.py price_tracker_v2.py` to see it:
the only changes are `fetch_page()`, one extra table in `init_db()`, and the
two helpers the new fetch layer needs (`backoff()`, `count_request()`).

## Setup

1. Install Python 3.11+, then:
   ```
   pip install -r requirements.txt
   ```
2. Copy the config template and fill in your own values:
   ```
   cp .env.example .env
   ```
3. Edit `products.json` (see below).
4. Run whichever version you want:
   ```
   python price_tracker_v1.py
   python price_tracker_v2.py
   ```

### Add products

`products.json` is a list, one object per product:

```json
[
  { "url": "https://www.amazon.in/dp/B08FYB5HHK", "target_price": 1000.0 },
  { "url": "https://www.amazon.in/dp/B09QYL69HM", "target_price": 170.0 }
]
```

Use the short `/dp/<ASIN>` form. `target_price` is in INR. The bot only ever
looks at the URLs in this file.

### Create a Telegram bot (needed by both versions)

1. Chat with `@BotFather` -> `/newbot` -> copy the token into `.env` as
   `TELEGRAM_BOT_TOKEN`.
2. Chat with `@userinfobot` (or message your bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates`) to find your numeric chat
   id -> put it in `.env` as `TELEGRAM_CHAT_ID`.
3. Send your bot a `/start` message first, otherwise it cannot message you.

### Get a GoLogin Web Unlocker key (needed by v2 only)

1. Sign up at GoLogin and get a Web Unlocker API key. The free trial includes
   **1,000 requests**.
2. Put it in `.env`:
   ```
   GOLOGIN_WEBUNLOCKER_API_KEY=your_key_here
   ```

## How v2 fetches a page

```
GET https://parsing.webunlocker.gologin.com/v1/scrape?url=<url-encoded target>
Header: apikey: <your key>
```

The response body is the raw rendered HTML, which is handed to exactly the same
parser v1 uses.

| Status | Meaning | What the script does |
|---|---|---|
| 200 | success (billable) | parse the HTML + increment the counter |
| 401 / 403 | bad or missing API key | clear error, **no retry** |
| 422 | bad url | clear error, **no retry** |
| 408 / 429 | timeout / rate limit | retry with backoff |
| 500-504 | temporary server error | retry with backoff |

Retries are 3 attempts total with exponential backoff (~2s, then ~4s, plus
jitter). Network errors are retried the same way.

A 200 is still checked by the normal v1 block detection: if the HTML contains
a captcha / "Robot Check" page, or no price can be found, the script logs
`BLOCKED: ...` and moves on.

### Request counter and cost

Every HTTP 200 is counted per month in the `api_usage` table of `prices_v2.db`
and logged as:

```
Web Unlocker requests used: 42 / 1000
```

The 1,000 is the free trial size. Billing is success-based, so only 200s count;
failed attempts are free. After the trial it is roughly **$0.98 per 1,000
successful pages** (about $0.001 per page). Two products checked every 6 hours
is about 240 pages/month.

## Run it on GitHub Actions

Add your secrets under **Settings -> Secrets and variables -> Actions**:

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (both workflows)
- `GOLOGIN_WEBUNLOCKER_API_KEY` (v2 only)

Then there are two separate workflows:

**`.github/workflows/price-check-v1.yml` - "Price check (v1 - plain requests)"**
Manual only (`workflow_dispatch`, no schedule). Run it from the Actions tab.
From a GitHub runner this will usually log `BLOCKED: status 503 ...` and finish
without sending alerts. That is the expected behaviour of a plain scraper on a
datacenter IP, not a bug in the script.

**`.github/workflows/price-check-v2.yml` - "Price check (v2 - GoLogin Web Unlocker)"**
Runs every 6 hours (`cron: "0 */6 * * *"`) plus a manual button.

Each workflow caches its own database (`prices-v1-` and `prices-v2-` cache key
prefixes, `prices.db` and `prices_v2.db`) so the two price histories never mix.
Note that GitHub evicts caches after ~7 days without use; if that happens the
history and the monthly counter simply restart, and the next run sends no
"price dropped" alert because there is no previous price to compare against.

## Scope / rules

Personal use, low volume, read-only. No auto-buying, no logging in to Amazon,
no account data, no checkout. The script only reads the public product pages
listed in `products.json` and compares prices. `.env`, `prices.db` and
`prices_v2.db` are gitignored - never commit your keys.
