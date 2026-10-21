"""Public-data market scanner. No broker login or order placement is used."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from app.strategy import evaluate_buyback, evaluate_sell

IST = ZoneInfo("Asia/Kolkata")
DATA_SOURCE = "Yahoo Finance (public feed; may be delayed)"
SCAN_INTERVAL_SECONDS = max(30, int(os.getenv("SCAN_INTERVAL_SECONDS", "30")))
MAX_DATA_AGE_MINUTES = 20


def yahoo_symbol(stock: dict) -> str:
    symbol = str(stock["symbol"]).upper()
    exchange = str(stock.get("exchange", "NSE")).upper()
    suffix = ".BO" if exchange == "BSE" else ".NS"
    if symbol.endswith((".NS", ".BO")):
        return symbol
    return symbol + suffix


def _frame_for(data, ticker: str):
    if data is None or getattr(data, "empty", True):
        return None
    columns = data.columns
    if getattr(columns, "nlevels", 1) > 1:
        if ticker in columns.get_level_values(0):
            return data[ticker]
        if ticker in columns.get_level_values(1):
            return data.xs(ticker, axis=1, level=1)
        return None
    return data


def _timestamp(value) -> datetime:
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=IST)
    return stamp.astimezone(IST)


def fetch_market_prices(stocks: list[dict], now: datetime | None = None) -> dict:
    """Batch-fetch today's 1-minute prices and the latest completed prior close."""
    if not stocks:
        return {}
    import yfinance as yf

    now = (now or datetime.now(IST)).astimezone(IST)
    ticker_to_stock = {}
    for stock in stocks:
        ticker_to_stock[yahoo_symbol(stock)] = stock

    tickers = list(ticker_to_stock)
    prices = {}
    # Chunk requests to avoid one enormous query when all five users have 40 stocks.
    for start in range(0, len(tickers), 40):
        batch = tickers[start:start + 40]
        try:
            intraday = yf.download(
                tickers=batch, period="1d", interval="1m", group_by="ticker",
                auto_adjust=False, prepost=False, progress=False, threads=True, timeout=20,
            )
        except Exception:
            intraday = None
        try:
            daily = yf.download(
                tickers=batch, period="5d", interval="1d", group_by="ticker",
                auto_adjust=False, prepost=False, progress=False, threads=True, timeout=20,
            )
        except Exception:
            daily = None

        for ticker in batch:
            intraday_frame = _frame_for(intraday, ticker)
            daily_frame = _frame_for(daily, ticker)
            latest_price = None
            latest_at = None
            if intraday_frame is not None and "Close" in intraday_frame:
                for index, row in intraday_frame.iterrows():
                    try:
                        stamp = _timestamp(index)
                        close = float(row["Close"])
                        if stamp.date() == now.date() and close > 0:
                            if latest_at is None or stamp > latest_at:
                                latest_at, latest_price = stamp, close
                    except (TypeError, ValueError):
                        continue

            previous_close = None
            if daily_frame is not None and "Close" in daily_frame:
                candidates = []
                for index, row in daily_frame.iterrows():
                    try:
                        stamp = _timestamp(index)
                        close = float(row["Close"])
                        if close > 0 and stamp.date() < now.date():
                            candidates.append((stamp, close))
                    except (TypeError, ValueError):
                        continue
                if candidates:
                    previous_close = max(candidates, key=lambda item: item[0])[1]

            # Never alert from an old intraday candle.
            if latest_at and now - latest_at <= timedelta(minutes=MAX_DATA_AGE_MINUTES):
                stock = ticker_to_stock[ticker]
                prices[(stock.get("exchange", "NSE"), stock["symbol"].upper())] = {
                    "price": latest_price,
                    "previous_close": previous_close,
                    "timestamp": latest_at,
                    "source": DATA_SOURCE,
                }
    return prices


def whatsapp_configured() -> bool:
    return bool(
        os.getenv("WHATSAPP_ACCESS_TOKEN")
        and os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        and os.getenv("WHATSAPP_RECIPIENTS")
    )


def send_whatsapp(message: str) -> str:
    """Send through Meta WhatsApp Cloud API when credentials are configured."""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    recipients = [item.strip() for item in os.getenv("WHATSAPP_RECIPIENTS", "").split(",") if item.strip()]
    if not token or not phone_id or not recipients:
        return "not_configured"

    version = os.getenv("WHATSAPP_API_VERSION", "v23.0")
    endpoint = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    outcomes = []
    for recipient in recipients:
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json={
                    "messaging_product": "whatsapp",
                    "to": recipient,
                    "type": "text",
                    "text": {"body": message},
                },
                timeout=10,
            )
            outcomes.append(response.ok)
        except requests.RequestException:
            outcomes.append(False)
    if all(outcomes):
        return "sent"
    if any(outcomes):
        return "partially_sent"
    return "failed"


def _market_window(now: datetime, start: tuple[int, int], end: tuple[int, int]) -> bool:
    local = now.astimezone(IST)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and start[0] * 60 + start[1] <= minute < end[0] * 60 + end[1]


def scan_once(database, now: datetime | None = None, fetcher=fetch_market_prices, notifier=send_whatsapp) -> dict:
    """Evaluate the agreed rules for every user's stocks, persist prices and deduplicated alerts."""
    now = (now or datetime.now(IST)).astimezone(IST)
    stocks = list(database.stocks.find({}))
    if not stocks:
        return {
            "status": "waiting_for_watchlist",
            "message": "Add stocks to begin scanning.",
            "last_scan": now.isoformat(),
            "stocks_scanned": 0,
            "source": DATA_SOURCE,
        }

    try:
        prices = fetcher(stocks, now=now)
    except Exception as exc:
        return {
            "status": "data_error",
            "message": f"Market data fetch failed: {type(exc).__name__}",
            "last_scan": now.isoformat(),
            "stocks_scanned": 0,
            "source": DATA_SOURCE,
        }

    scanned = 0
    alerts_created = 0
    sell_window = _market_window(now, (9, 15), (10, 45))
    buyback_window = _market_window(now, (9, 15), (15, 15))

    for stock in stocks:
        key = (stock.get("exchange", "NSE"), stock["symbol"].upper())
        market = prices.get(key)
        if not market:
            continue
        scanned += 1
        price = float(market["price"])
        previous_close = market.get("previous_close") or stock.get("previous_close")
        stock_id = stock["_id"]
        update_fields = {
            "market_price": price,
            "market_price_at": market["timestamp"].isoformat(),
            "market_data_source": market.get("source", DATA_SOURCE),
        }
        if market.get("previous_close"):
            update_fields["previous_close"] = float(market["previous_close"])
        database.stocks.update_one({"_id": stock_id}, {"$set": update_fields})
        # Persist price points for the user's live chart; history-write failures
        # must not prevent alert evaluation.
        try:
            database.price_history.insert_one({
                "user_id": stock["user_id"],
                "stock_id": str(stock_id),
                "symbol": stock["symbol"],
                "exchange": stock.get("exchange", "NSE"),
                "price": price,
                "timestamp": market["timestamp"].isoformat(),
                "source": market.get("source", DATA_SOURCE),
            })
        except Exception:
            pass

        sell_signal = None
        if previous_close:
            sell_signal = evaluate_sell(
                price=price,
                previous_close=float(previous_close),
                purchase_price=stock.get("purchase_price"),
                resistance=stock.get("resistance"),
                now=now,
            )
        buyback_signal = evaluate_buyback(
            price=price,
            actual_sell_price=stock.get("actual_sell_price"),
            now=now,
        )

        for kind, signal, active_field, in_window in (
            ("SELL", sell_signal, "sell_alert_active", sell_window),
            ("BUY_BACK", buyback_signal, "buyback_alert_active", buyback_window),
        ):
            was_active = bool(stock.get(active_field))
            if signal and not was_active:
                message = (
                    f"Tiny Stock Scanner — {signal.action}\n"
                    f"{stock['symbol']} ({stock.get('exchange', 'NSE')}) at ₹{signal.price:.2f}\n"
                    f"Reason: {signal.reason}\n"
                    f"Time: {now.strftime('%d-%b-%Y %H:%M:%S IST')}\n"
                    "Alert only — no order was placed."
                )
                delivery = notifier(message)
                database.alerts.insert_one({
                    "user_id": stock["user_id"],
                    "stock_id": str(stock_id),
                    "symbol": stock["symbol"],
                    "exchange": stock.get("exchange", "NSE"),
                    "action": signal.action,
                    "reason": signal.reason,
                    "price": signal.price,
                    "created_at": now.isoformat(),
                    "delivery_status": delivery,
                })
                database.stocks.update_one({"_id": stock_id}, {"$set": {active_field: True}})
                alerts_created += 1
            elif not signal and in_window and was_active:
                # Re-arm only after the trigger condition clears during its alert window.
                database.stocks.update_one({"_id": stock_id}, {"$set": {active_field: False}})

    return {
        "status": "ok" if scanned else "no_market_data",
        "message": (
            f"Scanned {scanned} stock(s); created {alerts_created} new alert(s)."
            if scanned else "No fresh public-market prices returned; no alerts were generated."
        ),
        "last_scan": now.isoformat(),
        "stocks_scanned": scanned,
        "alerts_created": alerts_created,
        "source": DATA_SOURCE,
    }
