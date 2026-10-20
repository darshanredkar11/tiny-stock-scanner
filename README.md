# Tiny Stock Scanner

Minimal alert-only NSE/BSE portfolio scanner foundation.

## Status
This commit provides a backend foundation, core signal rules, API authentication and portfolio CRUD, a Dockerfile, a Render Blueprint, and CI tests. It is **not yet a live autonomous scanner**: Zerodha OAuth/token renewal, KiteTicker sessions, alert deduplication, WhatsApp delivery, and a frontend remain to be implemented. Do not rely on it for live-market alerts yet.

## Strategy
- Up to 40 stocks per user.
- Sell signal at >=2% above previous close, >=1% above purchase price, or at/above configured resistance.
- Sell window 09:15 to 10:45 IST (10:45 excluded).
- Buy-back signal at >=1% below recorded actual sell price, before 15:15 IST (exclusive).
- Weekends blocked. No order placement exists.

## Local
```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -q
```

## Environment
- `JWT_SECRET`: set a strong random value in deployment.
- `DATABASE_PATH`: persistent SQLite path for single-instance evaluation only. Use managed PostgreSQL before public multi-instance launch.

The included Render configuration is for an evaluation deployment only. Do not expose publicly until OAuth state protection, rate limits, email verification/recovery, PostgreSQL, the live scanner worker, and alert delivery are implemented and tested.
