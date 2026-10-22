from datetime import datetime
from zoneinfo import ZoneInfo

import mongomock

from app.scanner import _chart_api_fallback, scan_once, yahoo_symbol

IST = ZoneInfo("Asia/Kolkata")


def test_yahoo_symbol_maps_nse_and_bse():
    assert yahoo_symbol({"symbol": "RELIANCE", "exchange": "NSE"}) == "RELIANCE.NS"
    assert yahoo_symbol({"symbol": "RELIANCE", "exchange": "BSE"}) == "RELIANCE.BO"
    assert yahoo_symbol({"symbol": "RELIANCE.NS", "exchange": "NSE"}) == "RELIANCE.NS"


def test_scanner_persists_market_price_and_deduplicates_sell_alerts():
    database = mongomock.MongoClient()["tss"]
    stock_id = database.stocks.insert_one({
        "user_id": "user-1",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "purchase_price": None,
        "previous_close": None,
        "resistance": None,
        "actual_sell_price": None,
        "sell_alert_active": False,
        "buyback_alert_active": False,
    }).inserted_id
    now = datetime(2026, 10, 20, 9, 30, tzinfo=IST)
    prices = {
        ("NSE", "RELIANCE"): {
            "price": 102.0,
            "previous_close": 100.0,
            "timestamp": now,
            "source": "Yahoo Finance (public feed; may be delayed)",
        }
    }
    fetcher = lambda stocks, now=None: prices
    sent = []
    notifier = lambda message: sent.append(message) or "sent"

    first = scan_once(database, now=now, fetcher=fetcher, notifier=notifier)
    second = scan_once(database, now=now, fetcher=fetcher, notifier=notifier)

    assert first["stocks_scanned"] == 1
    assert first["alerts_created"] == 1
    assert second["alerts_created"] == 0
    assert len(sent) == 1
    stock = database.stocks.find_one({"_id": stock_id})
    assert stock["market_price"] == 102.0
    assert stock["previous_close"] == 100.0
    alert = database.alerts.find_one({"user_id": "user-1"})
    assert alert["action"] == "SELL"
    assert alert["delivery_status"] == "sent"


def test_scanner_alerts_after_old_morning_cutoff_until_market_close():
    database = mongomock.MongoClient()["tss"]
    database.stocks.insert_one({
        "user_id": "user-1",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "purchase_price": None,
        "previous_close": 100.0,
        "resistance": None,
        "actual_sell_price": None,
        "sell_alert_active": False,
    })
    now = datetime(2026, 10, 20, 11, 0, tzinfo=IST)
    prices = {
        ("NSE", "RELIANCE"): {
            "price": 102.0,
            "previous_close": 100.0,
            "timestamp": now,
            "source": "Yahoo Finance (public feed; may be delayed)",
        }
    }
    result = scan_once(
        database, now=now, fetcher=lambda stocks, now=None: prices,
        notifier=lambda message: "sent",
    )
    assert result["alerts_created"] == 1
    assert database.alerts.count_documents({}) == 1



def test_chart_api_fallback_uses_fresh_today_candle_and_prior_day_close(monkeypatch):
    import app.scanner as scanner

    now = datetime(2026, 10, 20, 10, 0, tzinfo=IST)
    stamps = [
        int(datetime(2026, 10, 19, 15, 29, tzinfo=IST).timestamp()),
        int(datetime(2026, 10, 20, 9, 59, tzinfo=IST).timestamp()),
    ]

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"chart": {"result": [{
                "timestamp": stamps,
                "indicators": {"quote": [{"close": [100.0, 102.5]}]},
            }]}}

    calls = []
    def fake_get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(scanner.requests, "get", fake_get)
    result = _chart_api_fallback("RELIANCE.NS", now)

    assert result is not None
    assert result["price"] == 102.5
    assert result["previous_close"] == 100.0
    assert result["timestamp"] == datetime(2026, 10, 20, 9, 59, tzinfo=IST)
    assert "query1.finance.yahoo.com" in calls[0]
