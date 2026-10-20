import os, sqlite3, hashlib, hmac, base64, json, time
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DATABASE_PATH", "/tmp/tiny-stock-scanner.db")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
app = FastAPI(title="Tiny Stock Scanner", version="0.1.0")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS stocks(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        symbol TEXT NOT NULL, exchange TEXT NOT NULL CHECK(exchange IN ('NSE','BSE')), purchase_price REAL,
        previous_close REAL, resistance REAL, actual_sell_price REAL, UNIQUE(user_id, exchange, symbol));
    """)
    return conn

def hash_password(password: str, salt: bytes | None = None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
    return base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode()

def token_for(user_id: int):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": user_id, "exp": int(time.time()) + 86400}).encode()).rstrip(b"=")
    signature = hmac.new(JWT_SECRET.encode(), payload, hashlib.sha256).digest()
    return payload.decode() + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

def current_user(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        part, sig_part = authorization[7:].split(".", 1)
        expected = hmac.new(JWT_SECRET.encode(), part.encode(), hashlib.sha256).digest()
        sig = base64.urlsafe_b64decode(sig_part + "=" * (-len(sig_part) % 4))
        if not hmac.compare_digest(expected, sig): raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if payload["exp"] < int(time.time()): raise ValueError("expired")
        return int(payload["sub"])
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
    with db() as conn: conn.execute("SELECT 1")
    return {"status": "ok"}

@app.post("/api/register", status_code=201)
def register(body: Credentials):
    email = body.email.strip().lower()
    salt, digest = hash_password(body.password)
    try:
        with db() as conn:
            cur = conn.execute("INSERT INTO users(email,salt,password_hash) VALUES(?,?,?)", (email, salt, digest))
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")
    return {"access_token": token_for(user_id), "token_type": "bearer"}

@app.post("/api/login")
def login(body: Credentials):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
    if not row: raise HTTPException(401, "Invalid credentials")
    salt = base64.urlsafe_b64decode(row["salt"])
    _, digest = hash_password(body.password, salt)
    if not hmac.compare_digest(digest, row["password_hash"]): raise HTTPException(401, "Invalid credentials")
    return {"access_token": token_for(row["id"]), "token_type": "bearer"}

@app.get("/api/stocks")
def list_stocks(authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    with db() as conn:
        rows = conn.execute("SELECT id,symbol,exchange,purchase_price,previous_close,resistance,actual_sell_price FROM stocks WHERE user_id=? ORDER BY symbol", (user_id,)).fetchall()
    return [dict(row) for row in rows]

@app.post("/api/stocks", status_code=201)
def add_stock(body: StockInput, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM stocks WHERE user_id=?", (user_id,)).fetchone()[0]
        if count >= 40: raise HTTPException(400, "Maximum 40 stocks per user")
        try:
            cur = conn.execute("INSERT INTO stocks(user_id,symbol,exchange,purchase_price,previous_close,resistance) VALUES(?,?,?,?,?,?)",
                (user_id, body.symbol.upper(), body.exchange, body.purchase_price, body.previous_close, body.resistance))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Stock already exists in this portfolio")
        return {"id": cur.lastrowid, "symbol": body.symbol.upper(), "exchange": body.exchange}

@app.delete("/api/stocks/{stock_id}", status_code=204)
def delete_stock(stock_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    with db() as conn:
        cur = conn.execute("DELETE FROM stocks WHERE id=? AND user_id=?", (stock_id, user_id))
        if cur.rowcount == 0: raise HTTPException(404, "Stock not found")

@app.post("/api/stocks/{stock_id}/sale")
def record_sale(stock_id: int, body: SaleInput, authorization: Optional[str] = Header(default=None)):
    user_id = current_user(authorization)
    with db() as conn:
        cur = conn.execute("UPDATE stocks SET actual_sell_price=? WHERE id=? AND user_id=?", (body.actual_sell_price, stock_id, user_id))
        if cur.rowcount == 0: raise HTTPException(404, "Stock not found")
    return {"stock_id": stock_id, "actual_sell_price": body.actual_sell_price, "status": "recorded"}
