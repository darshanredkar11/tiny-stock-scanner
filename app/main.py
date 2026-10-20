import os
import hashlib
import hmac
import base64
import json
import time
from typing import Optional

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "tiny_stock_scanner")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
app = FastAPI(title="Tiny Stock Scanner", version="0.2.0")

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
_mongo_client = None

def configure_indexes(database):
    database.users.create_index([("email", ASCENDING)], unique=True)
    database.stocks.create_index(
        [("user_id", ASCENDING), ("exchange", ASCENDING), ("symbol", ASCENDING)],
        unique=True,
    )
    database.stocks.create_index([("user_id", ASCENDING)])

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
    previous_close: float | None = Field(default=None, gt=0)
    resistance: float | None = Field(default=None, gt=0)

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
        {"_id": ObjectId(user_id), "stock_count": {"$lt": 40}},
        {"$inc": {"stock_count": 1}},
    )
    if reserved.modified_count != 1:
        user = database.users.find_one({"_id": ObjectId(user_id)}, {"_id": 1})
        if not user:
            raise HTTPException(401, "User account no longer exists")
        raise HTTPException(400, "Maximum 40 stocks per user")
    symbol = body.symbol.upper()
    document = {
        "user_id": user_id,
        "symbol": symbol,
        "exchange": body.exchange,
        "purchase_price": body.purchase_price,
        "previous_close": body.previous_close,
        "resistance": body.resistance,
        "actual_sell_price": None,
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
        {"$set": {"actual_sell_price": body.actual_sell_price}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Stock not found")
    return {"stock_id": stock_id, "actual_sell_price": body.actual_sell_price, "status": "recorded"}
