# Tiny Stock Scanner

A small, private, alert-only NSE/BSE watchlist scanner for up to five users.

## Current status

The backend API and strategy tests run in GitHub Actions. MongoDB Atlas is now the intended persistence layer. This is **not yet a live market scanner**: Kite OAuth, live KiteTicker handling, alert deduplication, WhatsApp delivery, and the frontend are still pending. Do not rely on it for live-market alerts yet.

## Strategy

- Up to 40 stocks per user.
- Sell signal at >=2% above previous close, >=1% above purchase price, or at/above configured resistance.
- Sell alerts only from 09:15 to before 10:45 IST.
- Buy-back alert at >=1% below the recorded actual sell price, from 09:15 to before 15:15 IST.
- Weekends blocked. No order placement exists.
- Signals do not guarantee profit; spread, slippage, taxes, and fees can exceed small price moves.

## Local development

Python 3.12+ recommended.

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

Tests use an isolated in-memory MongoDB mock. Before deployment, set a real Atlas connection string as the `MONGODB_URI` environment variable in the hosting provider; never commit it or paste a password into source code. The first real-Atlas connectivity and persistence test remains pending until a connection string is configured.

## Deployment environment

- `MONGODB_URI`: Atlas connection string (set as a secret in the hosting provider).
- `MONGODB_DATABASE`: defaults to `tiny_stock_scanner`.
- `JWT_SECRET`: use a strong, unique random secret.
