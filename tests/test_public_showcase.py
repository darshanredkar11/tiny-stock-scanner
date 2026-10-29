from fastapi.testclient import TestClient
import mongomock

import app.main as main


def test_public_showcase_uses_real_sell_alerts_but_anonymizes_accounts(monkeypatch):
    database = mongomock.MongoClient()["tiny_stock_scanner_test"]
    monkeypatch.setattr(main, "get_db", lambda: database)
    database.users.insert_one({"email": "private@example.com"})
    user_id = "user-private-id"
    stock_id = database.stocks.insert_one({
        "user_id": user_id,
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "purchase_price": 100.0,
        "purchase_quantity": 10,
    }).inserted_id
    alert_time = "2026-10-28T10:00:00+05:30"
    database.alerts.insert_one({
        "user_id": user_id,
        "stock_id": str(stock_id),
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "SELL",
        "reason": "test trigger",
        "price": 105.0,
        "created_at": alert_time,
    })
    database.price_history.insert_many([
        {"user_id": user_id, "stock_id": str(stock_id), "timestamp": "2026-10-28T10:01:00+05:30", "price": 104.5},
        {"user_id": user_id, "stock_id": str(stock_id), "timestamp": "2026-10-28T10:02:00+05:30", "price": 103.5},
    ])

    response = TestClient(main.app).get("/api/public/showcase/alert-outcomes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcomes_are_simulated"] is True
    assert payload["real_trades_verified"] is False
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["account"] == "Account 01"
    assert row["sell_signal_price"] == 105.0
    assert row["gross_sale_pnl"] == 50.0
    assert row["simulated_buyback_price"] == 103.5
    assert row["simulated_buyback_saving"] == 15.0
    assert "private@example.com" not in response.text
    assert user_id not in response.text
