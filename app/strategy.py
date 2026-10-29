from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SELL_START = time(9, 15)
SELL_CUTOFF = time(15, 30)
BUYBACK_START = time(9, 15)
BUYBACK_CUTOFF = time(15, 30)

@dataclass(frozen=True)
class Signal:
    action: str
    reason: str
    price: float

def evaluate_sell(*, price: float, previous_close: float, purchase_price: float | None,
                  resistance: float | None, now: datetime, target_percent: float = 2.0) -> Signal | None:
    """Return an alert candidate only; this function never places orders."""
    local = now.astimezone(IST)
    clock = local.time().replace(tzinfo=None)
    if local.weekday() >= 5 or not (SELL_START <= clock < SELL_CUTOFF):
        return None
    if price <= 0 or previous_close <= 0:
        return None
    target_percent = min(50.0, max(0.1, float(target_percent)))
    if price >= previous_close * (1 + target_percent / 100):
        return Signal("SELL", f"at least {target_percent:g}% above previous close", price)
    if purchase_price is not None and purchase_price > 0 and price >= purchase_price * 1.01:
        return Signal("SELL", "at least 1% above purchase price", price)
    if resistance is not None and resistance > 0 and price >= resistance:
        return Signal("SELL", "resistance crossed", price)
    return None

def evaluate_buyback(*, price: float, actual_sell_price: float | None, now: datetime) -> Signal | None:
    """Return a buy-back alert candidate during market hours after an actual sale; never place orders."""
    local = now.astimezone(IST)
    clock = local.time().replace(tzinfo=None)
    if local.weekday() >= 5 or not (BUYBACK_START <= clock < BUYBACK_CUTOFF):
        return None
    if actual_sell_price is None or actual_sell_price <= 0 or price <= 0:
        return None
    if price <= actual_sell_price * 0.99:
        return Signal("BUY_BACK", "at least 1% below actual sell price", price)
    return None
