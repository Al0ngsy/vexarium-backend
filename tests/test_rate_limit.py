"""Tests for API rate limiting and request validation."""
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.main import app
from app.middleware.validation import validate_symbol


def make_synthetic_df(n=250):
    import numpy as np
    np.random.seed(42)
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": closes - 0.5, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": [1000000.0]*n,
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
    })


def test_validate_symbol_uppercases():
    assert validate_symbol("aapl") == "AAPL"
    assert validate_symbol("spy") == "SPY"


def test_validate_symbol_invalid():
    with pytest.raises(HTTPException) as exc_info:
        validate_symbol("bad symbol!")
    assert exc_info.value.status_code == 422


def test_validate_symbol_rejects_bad_symbols():
    for bad in ["", "12345678901", "AA-BB", "BAD!"]:
        with pytest.raises(HTTPException):
            validate_symbol(bad)


@pytest.mark.asyncio
async def test_analysis_works_with_valid_input():
    df = make_synthetic_df()
    from app.main import limiter
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch("app.api.analysis.AlpacaClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.get_stock_bars.return_value = df
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/analysis",
                    json={"symbol": "aapl", "asset_type": "stock"},
                )
                assert resp.status_code == 200
                assert resp.json()["symbol"] == "AAPL"
    finally:
        limiter.enabled = prev


@pytest.mark.asyncio
async def test_analysis_invalid_symbol_returns_422():
    from app.main import limiter
    prev = limiter.enabled
    limiter.enabled = False
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/analysis",
                json={"symbol": "bad symbol!", "asset_type": "stock"},
            )
            assert resp.status_code == 422
    finally:
        limiter.enabled = prev


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429():
    """A dedicated tiny limiter returns 429 on the second request."""
    small = Limiter(key_func=get_remote_address)
    mini_app = FastAPI()
    mini_app.state.limiter = small
    mini_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @mini_app.get("/limited")
    @small.limit("1/minute")
    async def limited(request: Request):
        return {"ok": True}

    transport = ASGITransport(app=mini_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/limited")
        second = await client.get("/limited")
    assert first.status_code == 200
    assert second.status_code == 429
