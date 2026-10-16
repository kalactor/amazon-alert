# Amazon India Price Tracker v1

Simple read-only tracker. Sends a Telegram message when price <= target or drops >=5%.

## Setup

1. Install Python 3.11, then:
   pip install -r requirements.txt
2. Copy config:
   cp .env.example .env
3. Edit `products.json` with your `url` + `target_price` (INR).
4. Run:
   python price_tracker_v1.py
   Schedule with cron or GitHub Actions to run hourly/daily.

## Create a Telegram bot

1. Chat with `@BotFather` -> `/newbot` -> copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Chat with `@userinfobot` (or send a message to your bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates`) to find your numeric
   chat `id` -> put it in `.env` as `TELEGRAM_CHAT_ID`.
3. Send your bot a `/start` message first, otherwise it cannot DM you.

## Note

v1 uses plain `requests` with one User-Agent on purpose. Expect HTTP 403/503
captcha blocks - the script logs `BLOCKED: status ...` and moves on.
