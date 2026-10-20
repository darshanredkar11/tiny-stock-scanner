from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from functools import partial

import pytest
from playwright.sync_api import Page, expect


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture
def dashboard_url():
    handler = partial(QuietHandler, directory=str(STATIC_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.fixture
def fake_api(page: Page):
    state = {"stocks": [], "alerts": [], "next_id": 1, "calls": []}

    def handle(route):
        request = route.request
        path = request.url.split("127.0.0.1", 1)[-1].split("?", 1)[0]
        method = request.method
        state["calls"].append((method, path))
        payload = json.loads(request.post_data) if request.post_data else {}

        if path in ("/api/register", "/api/login") and method == "POST":
            route.fulfill(status=201 if path.endswith("register") else 200,
                          json={"access_token": "browser-test-token", "token_type": "bearer"})
            return
        if path == "/api/stocks" and method == "GET":
            route.fulfill(json=state["stocks"])
            return
        if path == "/api/stocks" and method == "POST":
            symbol = payload.get("symbol", "").upper()
            if len(state["stocks"]) >= 40:
                route.fulfill(status=400, json={"detail": "Maximum 40 stocks per user"})
                return
            if any(s["symbol"] == symbol and s["exchange"] == payload.get("exchange")
                   for s in state["stocks"]):
                route.fulfill(status=409, json={"detail": "Stock already exists"})
                return
            stock = {
                **payload,
                "symbol": symbol,
                "id": str(state["next_id"]),
                "market_price": 123.45,
                "market_price_at": "2026-10-21T04:00:00Z",
                "actual_sell_price": None,
            }
            state["next_id"] += 1
            state["stocks"].append(stock)
            route.fulfill(status=201, json=stock)
            return
        if path == "/api/market/status" and method == "GET":
            route.fulfill(json={
                "status": "ok", "message": "Test feed ready", "source": "Mock market feed",
                "interval_seconds": 60, "whatsapp_configured": False,
            })
            return
        if path == "/api/alerts" and method == "GET":
            route.fulfill(json=state["alerts"])
            return
        if path.startswith("/api/stocks/") and path.endswith("/sale") and method == "POST":
            stock_id = path.split("/")[-2]
            stock = next((s for s in state["stocks"] if s["id"] == stock_id), None)
            if not stock:
                route.fulfill(status=404, json={"detail": "Stock not found"})
                return
            stock["actual_sell_price"] = payload["actual_sell_price"]
            route.fulfill(json=stock)
            return
        if path.startswith("/api/stocks/") and method == "DELETE":
            stock_id = path.rsplit("/", 1)[-1]
            state["stocks"] = [s for s in state["stocks"] if s["id"] != stock_id]
            route.fulfill(status=204, body="")
            return
        route.fulfill(status=404, json={"detail": f"Unmocked API route: {method} {path}"})

    page.route("**/api/**", handle)
    return state


def create_account(page: Page, dashboard_url: str):
    page.goto(dashboard_url)
    page.get_by_role("button", name="Create account").click()
    page.locator("#email").fill("tester@example.com")
    page.locator("#password").fill("CorrectHorseBattery!42")
    page.locator("#authBtn").click()
    expect(page.locator("#appPanel")).to_be_visible()


def add_stock(page: Page, symbol="RELIANCE"):
    page.locator("#symbol").fill(symbol)
    page.locator("#exchange").select_option("NSE")
    page.locator("#purchase").fill("120")
    page.locator("#previous").fill("121")
    page.locator("#resistance").fill("130")
    page.locator("#addBtn").click()


def test_register_add_stock_and_show_scanner_status(page: Page, dashboard_url: str, fake_api):
    create_account(page, dashboard_url)
    add_stock(page)

    expect(page.locator("#stocksBody")).to_contain_text("RELIANCE")
    expect(page.locator("#stocksBody")).to_contain_text("NSE")
    expect(page.locator("#stocksBody")).to_contain_text("123.45")
    expect(page.locator("#countText")).to_have_text("1 / 40 stocks")
    expect(page.locator("#marketStatus")).to_contain_text("OK")
    expect(page.locator("#marketDetails")).to_contain_text("Mock market feed")
    expect(page.locator("#emptyText")).to_be_hidden()
    assert ("POST", "/api/register") in fake_api["calls"]
    assert ("GET", "/api/market/status") in fake_api["calls"]
    assert ("GET", "/api/alerts") in fake_api["calls"]


def test_record_sale_price_for_buyback_tracking(page: Page, dashboard_url: str, fake_api):
    create_account(page, dashboard_url)
    add_stock(page, "TCS")

    page.get_by_role("button", name="Record sale").click()
    expect(page.locator("#stockStatus")).to_contain_text("valid actual sell price")

    page.get_by_label("Actual sell price for TCS").fill("125")
    page.get_by_role("button", name="Record sale").click()
    expect(page.locator("#stockStatus")).to_contain_text("Actual sale price recorded")
    expect(page.locator("#stocksBody")).to_contain_text("125")
    assert ("POST", "/api/stocks/1/sale") in fake_api["calls"]


def test_symbol_required_and_logout(page: Page, dashboard_url: str, fake_api):
    create_account(page, dashboard_url)
    page.locator("#addBtn").click()
    expect(page.locator("#stockStatus")).to_have_text("Enter a stock symbol.")
    expect(page.locator("#stocksBody tr")).to_have_count(0)

    page.locator("#logoutBtn").click()
    expect(page.locator("#authPanel")).to_be_visible()
    expect(page.locator("#appPanel")).to_be_hidden()
