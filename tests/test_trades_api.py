"""Tests for the trades API (Task 21): CRUD with JWT auth + ownership isolation."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _register(email: str, password: str = "secret123") -> str:
    """Register a user and return the access token."""
    resp = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _create_trade(token: str, symbol: str = "AAPL", price: float = 150.0) -> dict:
    resp = client.post(
        f"/api/v1/trades?token={token}",
        json={
            "symbol": symbol,
            "trade_type": "stock",
            "entry_date": "2026-01-01",
            "entry_price": price,
        },
    )
    assert resp.status_code == 201
    return resp.json()


def test_create_trade_requires_auth():
    resp = client.post(
        "/api/v1/trades",
        json={"symbol": "AAPL", "trade_type": "stock", "entry_date": "2026-01-01", "entry_price": 150.0},
    )
    assert resp.status_code == 401


def test_create_and_list_trade():
    token = _register("trade_alice@example.com")
    trade = _create_trade(token)

    assert trade["symbol"] == "AAPL"
    assert trade["user_id"] is not None

    resp = client.get(f"/api/v1/trades?token={token}")
    assert resp.status_code == 200
    trades = resp.json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["entry_price"] == 150.0


def test_delete_trade():
    token = _register("trade_bob@example.com")
    trade = _create_trade(token)
    trade_id = trade["id"]

    resp = client.delete(f"/api/v1/trades/{trade_id}?token={token}")
    assert resp.status_code == 204

    resp = client.get(f"/api/v1/trades?token={token}")
    assert resp.status_code == 200
    assert resp.json() == []


def test_cannot_access_other_users_trades():
    token_a = _register("trade_owner@example.com")
    token_b = _register("trade_other@example.com")

    _create_trade(token_a, symbol="MSFT")

    resp = client.get(f"/api/v1/trades?token={token_b}")
    assert resp.status_code == 200
    assert resp.json() == []

    # Owner still sees their trade.
    resp = client.get(f"/api/v1/trades?token={token_a}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
