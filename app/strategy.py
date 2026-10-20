from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SELL_START = time(9, 15)
SELL_CUTOFF = time(10, 45)
BUYBACK_CUTOFF = time(15, 15)

@dataclass(frozen=True)
class Signal:
    action: str
    reason: str
    price: float

def evaluate_sell(*, price: float, previous_close: float, purchase_price: float | None,
                  resistance: float | None, now: datetime) -> Signal | None:
    """Return an alert candidate only; this function never places orders."""
    local = now.astimezone(IST)
    clock = local.time().replace(tzinfo=None)
    if local.weekday() >= 5 or not (SELL_START <= clock < SELL_CUTOFF):
        return None
    if price <= 0 or previous_close <= 0:
        return None
    if price >= previous_close * 1.02:
        return Signal("SELL", "at least 2% above previous close", price)
    if purchase_price is not None and purchase_price > 0 and price >= purchase_price * 1.01:
        return Signal("SELL", "at least 1% above purchase price", price)
    if resistance is not None and resistance > 0 and price >= resistance:
        return Signal("SELL", "resistance crossed", price)
    return None

def evaluate_buyback(*, price: float, actual_sell_price: float | None, now: datetime) -> Signal | None:
    """Return a buy-back alert candidate after an actual sale; never place orders."""
    local = now.astimezone(IST)
    clock = local.time().replace(tzinfo=None)
    if local.weekday() >= 5 or clock >= BUYBACK_CUTOFF:
        return None
    if actual_sell_price is None or actual_sell_price <= 0 or price <= 0:
        return None
    if price <= actual_sell_price * 0.99:
        return Signal("BUY_BACK", "at least 1% below actual sell price", price)
    return None
