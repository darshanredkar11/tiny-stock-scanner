import os
import hashlib
import hmac
import base64
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import threading
import logging
from typing import Optional

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from app.scanner import DATA_SOURCE, SCAN_INTERVAL_SECONDS, scan_once, whatsapp_configured

logger = logging.getLogger("uvicorn.error")

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "tiny_stock_scanner")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
app = FastAPI(title="Tiny Stock Scanner", version="0.3.0")
_scanner_status = {
    "status": "starting",
    "message": "Waiting for the scanner worker to start.",
    "last_scan": None,
    "stocks_scanned": 0,
    "source": DATA_SOURCE,
}
_scanner_thread = None


def _scanner_loop():
    while True:
        cycle_started = time.monotonic()
        try:
            result = scan_once(get_db())
            _scanner_status.update(result)
            logger.info(
                "scanner_cycle status=%s stocks_scanned=%s alerts_created=%s duration_ms=%s source=%s message=%s",
                result.get("status"),
                result.get("stocks_scanned", 0),
                result.get("alerts_created", 0),
                int((time.monotonic() - cycle_started) * 1000),
                result.get("source", DATA_SOURCE),
                result.get("message", ""),
            )
        except Exception as exc:
            _scanner_status.update({
                "status": "error",
                "message": f"Scanner cycle failed: {type(exc).__name__}",
                "last_scan": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": DATA_SOURCE,
            })
            logger.exception("scanner_cycle_failed exception_type=%s", type(exc).__name__)
        # Keep the interval measured from cycle start rather than adding the
        # full interval after slow provider calls. Never spin on a slow cycle.
        time.sleep(max(1, SCAN_INTERVAL_SECONDS - (time.monotonic() - cycle_started)))


@app.on_event("startup")
def start_scanner_worker():
    global _scanner_thread
    enabled = os.getenv("SCANNER_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
    if MONGODB_URI and enabled and (_scanner_thread is None or not _scanner_thread.is_alive()):
        _scanner_thread = threading.Thread(target=_scanner_loop, name="market-scanner", daemon=True)
        _scanner_thread.start()
        logger.info("scanner_worker_started interval_seconds=%s source=%s", SCAN_INTERVAL_SECONDS, DATA_SOURCE)
    elif not MONGODB_URI:
        logger.error("scanner_worker_not_started reason=mongodb_uri_missing")
    elif not enabled:
        logger.warning("scanner_worker_disabled_by_configuration")

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

@app.get("/api/showcase/stats")
def public_showcase_stats():
    database = get_db()
    return {
        "onboarded_users": database.users.count_documents({}),
        "stocks_tracked": database.stocks.count_documents({}),
        "alerts_generated": database.alerts.count_documents({}),
        "updated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }


@app.get("/api/public/showcase/alert-outcomes")
def public_showcase_alert_outcomes():
    """Public, anonymized simulations calculated from real stored SELL alerts and price history."""
    database = get_db()
    alerts = list(database.alerts.find({"action": "SELL"}).sort("created_at", -1).limit(100))
    user_ids = sorted({str(alert.get("user_id", "")) for alert in alerts if alert.get("user_id")})
    account_labels = {user_id: f"Account {index + 1:02d}" for index, user_id in enumerate(user_ids)}
    rows = []

    for alert in alerts:
        user_id = str(alert.get("user_id", ""))
        stock_id = str(alert.get("stock_id", ""))
        created_at = str(alert.get("created_at", ""))
        try:
            sell_price = float(alert.get("price"))
        except (TypeError, ValueError):
            continue

        stock = None
        try:
            stock = database.stocks.find_one({"_id": ObjectId(stock_id), "user_id": user_id})
        except Exception:
            stock = None

        quantity = None
        purchase_price = None
        if stock:
            try:
                quantity = float(stock.get("purchase_quantity")) if stock.get("purchase_quantity") is not None else None
            except (TypeError, ValueError):
                quantity = None
            try:
                purchase_price = float(stock.get("purchase_price")) if stock.get("purchase_price") is not None else None
            except (TypeError, ValueError):
                purchase_price = None

        gross_sale_pnl = None
        if quantity is not None and purchase_price is not None:
            gross_sale_pnl = round((sell_price - purchase_price) * quantity, 2)

        # Simulate a 1% lower re-entry using subsequent recorded price points
        # from the same trading date. This is not evidence that a user traded.
        buyback_price = None
        if user_id and stock_id and created_at and "T" in created_at:
            trading_date = created_at[:10]
            end_of_day = trading_date + "T23:59:59.999999+05:30"
            history = database.price_history.find({
                "user_id": user_id,
                "stock_id": stock_id,
                "timestamp": {"$gt": created_at, "$lt": end_of_day},
            }).sort("timestamp", 1)
            target_price = sell_price * 0.99
            for point in history:
                try:
                    point_price = float(point.get("price"))
                except (TypeError, ValueError):
                    continue
                if point_price > 0 and point_price <= target_price:
                    buyback_price = round(point_price, 2)
                    break

        buyback_saving = None
        if buyback_price is not None and quantity is not None:
            buyback_saving = round((sell_price - buyback_price) * quantity, 2)

        rows.append({
            "account": account_labels.get(user_id, "Account"),
            "symbol": alert.get("symbol", "—"),
            "exchange": alert.get("exchange", "NSE"),
            "alert_time": created_at,
            "sell_signal_price": round(sell_price, 2),
            "configured_purchase_price": round(purchase_price, 2) if purchase_price is not None else None,
            "configured_quantity": quantity,
            "gross_sale_pnl": gross_sale_pnl,
            "simulated_buyback_price": buyback_price,
            "simulated_buyback_saving": buyback_saving,
            "buyback_status": "1% lower price observed later that day" if buyback_price is not None else "No matching 1% lower price point recorded later that day",
            "alert_reason": alert.get("reason", ""),
        })

    return {
        "rows": rows,
        "source": "Stored SELL alerts and recorded price history",
        "outcomes_are_simulated": True,
        "real_trades_verified": False,
        "updated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }


_mongo_client = None

def configure_indexes(database):
    database.users.create_index([("email", ASCENDING)], unique=True)
    database.stocks.create_index(
        [("user_id", ASCENDING), ("exchange", ASCENDING), ("symbol", ASCENDING)],
        unique=True,
    )
    database.stocks.create_index([("user_id", ASCENDING)])
    database.price_history.create_index([("user_id", ASCENDING), ("stock_id", ASCENDING), ("timestamp", ASCENDING)])

def get_db():
    global _mongo_client
    if not MONGODB_URI:
        raise HTTPException(503, "MongoDB is not configured; set MONGODB_URI")
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = _mongo_client[MONGODB_DATABASE]
    configure_indexes(database)
    return database

def hash_password(password: str, salt: bytes | None = None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
    return base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode()

def token_for(user_id: str):
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": str(user_id), "exp": int(time.time()) + 86400}).encode()
    ).rstrip(b"=")
    signature = hmac.new(JWT_SECRET.encode(), payload, hashlib.sha256).digest()
    return payload.decode() + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

def current_user(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        part, sig_part = authorization[7:].split(".", 1)
        expected = hmac.new(JWT_SECRET.encode(), part.encode(), hashlib.sha256).digest()
        sig = base64.urlsafe_b64decode(sig_part + "=" * (-len(sig_part) % 4))
        if not hmac.compare_digest(expected, sig):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if payload["exp"] <= int(time.time()):
            raise ValueError("expired")
        return str(payload["sub"])
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)

class StockInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9&._-]+$")
    exchange: str = Field(pattern=r"^(NSE|BSE)$")
    purchase_price: float | None = Field(default=None, gt=0)
    purchase_quantity: float | None = Field(default=None, gt=0)
    previous_close: float | None = Field(default=None, gt=0)
    resistance: float | None = Field(default=None, gt=0)
    sell_target_percent: float = Field(default=2.0, ge=0.1, le=50)

class SellTargetInput(BaseModel):
    sell_target_percent: float = Field(ge=0.1, le=50)

class SaleInput(BaseModel):
    actual_sell_price: float = Field(gt=0)

@app.get("/health")
def health():
    database = get_db()
    database.users.count_documents({})
    return {"status": "ok"}

@app.post("/api/register", status_code=201)
def register(body: Credentials):
    database = get_db()
    email = body.email.strip().lower()
    salt, digest = hash_password(body.password)
    try:
        result = database.users.insert_one({
            "email": email,
            "salt": salt,
            "password_hash": digest,
            "stock_count": 0,
        })
    except DuplicateKeyError:
        raise HTTPException(409, "Email already registered")
    user_id = str(result.inserted_id)
    return {"access_token": token_for(user_id), "token_type": "bearer"}

@app.post("/api/login")
def login(body: Credentials):
    database = get_db()
    row = database.users.find_one({"email": body.email.strip().lower()})
    if not row:
        raise HTTPException(401, "Invalid credentials")
    salt = base64.urlsafe_b64decode(row["salt"])
    _, digest = hash_password(body.password, salt)
    if not hmac.compare_digest(digest, row["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": token_for(str(row["_id"])), "token_type": "bearer"}

@app.get("/api/stocks")
def list_stocks(authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    database = get_db()
    rows = database.stocks.find({"user_id": user_id}).sort("symbol", ASCENDING)
    result = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in ("_id", "user_id")}
        item["id"] = str(row["_id"])
        result.append(item)
    return result

@app.post("/api/stocks", status_code=201)
def add_stock(body: StockInput, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    database = get_db()
    reserved = database.users.update_one(
        {"_id": ObjectId(user_id), "stock_count": {"$lt": 60}},
        {"$inc": {"stock_count": 1}},
    )
    if reserved.modified_count != 1:
        user = database.users.find_one({"_id": ObjectId(user_id)}, {"_id": 1})
        if not user:
            raise HTTPException(401, "User account no longer exists")
        raise HTTPException(400, "Maximum 60 stocks per user")
    symbol = body.symbol.upper()
    document = {
        "user_id": user_id,
        "symbol": symbol,
        "exchange": body.exchange,
        "purchase_price": body.purchase_price,
        "purchase_quantity": body.purchase_quantity,
        "previous_close": body.previous_close,
        "resistance": body.resistance,
        "sell_target_percent": body.sell_target_percent,
        "actual_sell_price": None,
        "sell_alert_active": False,
        "buyback_alert_active": False,
    }
    try:
        result = database.stocks.insert_one(document)
    except DuplicateKeyError:
        database.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"stock_count": -1}})
        raise HTTPException(409, "Stock already exists in this portfolio")
    except Exception:
        database.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"stock_count": -1}})
        raise
    return {"id": str(result.inserted_id), "symbol": symbol, "exchange": body.exchange}

@app.patch("/api/stocks/{stock_id}/settings")
def update_stock_settings(stock_id: str, body: SellTargetInput, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    try:
        object_id = ObjectId(stock_id)
    except Exception:
        raise HTTPException(404, "Stock not found")
    database = get_db()
    result = database.stocks.update_one(
        {"_id": object_id, "user_id": user_id},
        {"$set": {"sell_target_percent": body.sell_target_percent}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Stock not found")
    return {"stock_id": stock_id, "sell_target_percent": body.sell_target_percent, "status": "updated"}


@app.delete("/api/stocks/{stock_id}", status_code=204)
def delete_stock(stock_id: str, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    try:
        object_id = ObjectId(stock_id)
    except Exception:
        raise HTTPException(404, "Stock not found")
    database = get_db()
    result = database.stocks.delete_one({"_id": object_id, "user_id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Stock not found")
    database.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"stock_count": -1}})

@app.post("/api/stocks/{stock_id}/sale")
def record_sale(stock_id: str, body: SaleInput, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    try:
        object_id = ObjectId(stock_id)
    except Exception:
        raise HTTPException(404, "Stock not found")
    database = get_db()
    result = database.stocks.update_one(
        {"_id": object_id, "user_id": user_id},
        {"$set": {"actual_sell_price": round(body.actual_sell_price, 2),
                  "actual_sell_price_date": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
                  "buyback_alert_active": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Stock not found")
    return {"stock_id": stock_id, "actual_sell_price": body.actual_sell_price, "status": "recorded"}


@app.get("/api/market/status")
def market_status(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    return {
        **_scanner_status,
        "interval_seconds": SCAN_INTERVAL_SECONDS,
        "whatsapp_configured": whatsapp_configured(),
        "scanner_enabled": bool(MONGODB_URI) and os.getenv("SCANNER_ENABLED", "true").strip().lower() not in {"0", "false", "no"},
        "broker_login_required": False,
        "source": DATA_SOURCE,
    }


@app.get("/api/market/history")
def market_history(authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    database = get_db()
    rows = database.price_history.find({"user_id": user_id}).sort("timestamp", -1).limit(7200)
    grouped = {}
    counts = {}
    for row in rows:
        key = f"{row.get('exchange', 'NSE')}:{row.get('symbol', '')}"
        if counts.get(key, 0) >= 180:
            continue
        grouped.setdefault(key, []).append({
            "price": row.get("price"),
            "timestamp": row.get("timestamp"),
            "source": row.get("source", DATA_SOURCE),
        })
        counts[key] = counts.get(key, 0) + 1
    for points in grouped.values():
        points.reverse()
    return grouped


@app.get("/api/alerts")
def list_alerts(authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    database = get_db()
    rows = database.alerts.find({"user_id": user_id}).sort("created_at", -1).limit(50)
    result = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in ("_id", "user_id")}
        item["id"] = str(row["_id"])
        result.append(item)
    return result
