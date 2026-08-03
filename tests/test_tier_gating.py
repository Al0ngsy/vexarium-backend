"""Tests for tier-based feature gating (Task 19)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import _users, set_tier
from app.middleware.tier_gating import get_user_tier, require_tier
from app.services.auth import decode_token

client = TestClient(app)


def test_get_user_tier_default():
    assert get_user_tier("") == "free"


def test_require_tier_free_allowed():
    dep = require_tier("free")
    assert dep("") == "free"


def test_require_tier_pro_denied():
    dep = require_tier("pro")
    with pytest.raises(HTTPException) as excinfo:
        dep("")
    assert excinfo.value.status_code == 403


def _register(email: str, password: str = "secret123") -> dict:
    resp = client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201
    return resp.json()


def test_extended_endpoint_denied():
    # No token -> free tier -> 403 on the pro-only /extended endpoint.
    resp = client.post(
        "/api/v1/analysis/extended",
        json={"symbol": "AAPL", "asset_type": "stock"},
    )
    assert resp.status_code == 403


def test_extended_endpoint_allowed(monkeypatch):
    import pandas as pd
    import numpy as np
    from unittest.mock import patch

    def make_synthetic_df(n=250):
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame({
            "open": closes - 0.5, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": [1000000.0] * n,
            "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
        })

    body = _register("pro.user@example.com")
    token = body["access_token"]
    uid = int(decode_token(token)["sub"])
    # Manually escalate to pro tier.
    set_tier(uid, "pro")

    df = make_synthetic_df()
    with patch("app.api.analysis.AlpacaClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        resp = client.post(
            "/api/v1/analysis/extended",
            params={"token": token},
            json={"symbol": "AAPL", "asset_type": "stock"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "AAPL"
    assert "overall" in data
    assert "indicators" in data
    # Free (5) + pro (5) = 10 indicators.
    assert len(data["indicators"]) == 10
