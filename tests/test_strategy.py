from datetime import datetime
from zoneinfo import ZoneInfo
from app.strategy import evaluate_sell, evaluate_buyback

IST = ZoneInfo("Asia/Kolkata")

def dt(day, hour, minute):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)

def test_sell_two_percent_triggers_in_window():
    assert evaluate_sell(price=102, previous_close=100, purchase_price=None, resistance=None, now=dt(20,9,30)).reason.startswith("at least 2%")

def test_sell_purchase_gain_triggers():
    assert evaluate_sell(price=101, previous_close=100, purchase_price=100, resistance=None, now=dt(20,10,0))

def test_sell_resistance_triggers():
    assert evaluate_sell(price=105, previous_close=104, purchase_price=None, resistance=105, now=dt(20,10,0)).reason == "resistance crossed"

def test_sell_cutoff_is_exclusive_at_market_close():
    assert evaluate_sell(price=110, previous_close=100, purchase_price=None, resistance=None, now=dt(20,15,30)) is None

def test_sell_window_opens_at_0915():
    assert evaluate_sell(price=102, previous_close=100, purchase_price=None, resistance=None, now=dt(20,9,15))

def test_sell_below_all_thresholds_does_not_trigger():
    assert evaluate_sell(price=100.5, previous_close=100, purchase_price=100, resistance=105, now=dt(20,9,30)) is None

def test_sell_before_open_is_blocked():
    assert evaluate_sell(price=110, previous_close=100, purchase_price=None, resistance=None, now=dt(20,9,14)) is None

def test_buyback_at_one_percent_below_sale():
    assert evaluate_buyback(price=99, actual_sell_price=100, now=dt(20,14,0))

def test_buyback_window_opens_at_0915():
    assert evaluate_buyback(price=99, actual_sell_price=100, now=dt(20,9,15))

def test_buyback_before_open_is_blocked():
    assert evaluate_buyback(price=98, actual_sell_price=100, now=dt(20,9,14)) is None

def test_buyback_cutoff_is_exclusive_at_market_close():
    assert evaluate_buyback(price=98, actual_sell_price=100, now=dt(20,15,30)) is None

def test_buyback_requires_actual_sale():
    assert evaluate_buyback(price=98, actual_sell_price=None, now=dt(20,14,0)) is None

def test_weekend_is_blocked():
    assert evaluate_sell(price=110, previous_close=100, purchase_price=None, resistance=None, now=dt(24,9,30)) is None
