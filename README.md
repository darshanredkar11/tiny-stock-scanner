# Tiny Stock Scanner

A private, alert-only NSE/BSE scanner for up to five users, with up to 40 stocks per user. It does not place orders and does not require a Zerodha login.

## Main functionality

- Uses the public Yahoo Finance feed for NSE symbols (`.NS`) and BSE symbols (`.BO`); feed availability and timing can vary and prices may be delayed.
- Scanner checks watchlists every 60 seconds while the service process is running.
- Sell alert if price is at least 2% above the previous close, at least 1% above purchase price, or at/above configured resistance.
- Sell alerts only from 09:15 until before 10:45 India time.
- Buy-back alert if price is at least 1% below the recorded actual sale price, from 09:15 until before 15:15 India time.
- Alerts are deduplicated while a trigger remains active, stored in MongoDB, and shown in the dashboard.
- WhatsApp delivery uses Meta WhatsApp Cloud API when credentials are configured.
- No automatic order placement. Brokerage, taxes, spread, slippage, data delays, and failed delivery can affect results.

## Public market-data limitations

Yahoo Finance is a public, unofficial data source. It can delay, omit, or rate-limit symbols and does not provide an uptime or real-time guarantee. The scanner ignores intraday candles older than 20 minutes. This should be treated as a convenience alerting tool, not a guaranteed real-time trading feed.

The web service's background scanner runs only while the Render service process is awake. Render's free web service can spin down after inactivity, so continuous cloud monitoring requires an always-on service/worker or a scheduled worker that runs during market hours. Do not assume the free web service alone provides uninterrupted scanning.

## Configure environment variables in Render

Required:
- `MONGODB_URI`: MongoDB Atlas connection string, stored as a secret.
- `MONGODB_DATABASE=tiny_stock_scanner`
- `JWT_SECRET`: a long, unique random secret.

Optional WhatsApp Cloud API:
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_RECIPIENTS`: comma-separated recipient numbers in international format, e.g. `+9198XXXXXXXX`.
- `WHATSAPP_API_VERSION`: defaults to `v23.0`; use a supported Graph API version for your Meta app.

Meta WhatsApp Cloud API setup, business verification, recipient permissions, and approved templates may be required. The scanner stores alerts even if WhatsApp is not configured, and the dashboard displays delivery status.

Optional:
- `SCANNER_ENABLED=false` disables the background scanner.
- `SCAN_INTERVAL_SECONDS=60` changes the polling interval (minimum 30 seconds).

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
export MONGODB_URI='mongodb://localhost:27017'
export MONGODB_DATABASE='tiny_stock_scanner'
export JWT_SECRET='replace-with-a-long-random-secret'
uvicorn app.main:app --reload
python -m pytest -q
```

Tests use an isolated in-memory MongoDB mock and fake market data. They do not make live market requests or send WhatsApp messages.

## Endpoints

- `/`: dashboard
- `/health`: database connectivity health check
- `/api/market/status`: authenticated scanner status
- `/api/alerts`: authenticated alert history
- `/api/stocks`: authenticated per-user watchlist management
