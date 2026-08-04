import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

import app.api.assets as assets_module
from app.main import app


@pytest.mark.asyncio
async def test_asset_search_returns_matching_prefix():
    assets_module._assets_cache = [
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "asset_type": "stock"},
        {"symbol": "MSFT", "name": "Microsoft Corp", "exchange": "NASDAQ", "asset_type": "stock"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "exchange": "ARCA", "asset_type": "etf"},
    ]
    assets_module._assets_loaded = True
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/assets/search", params={"q": "AAPL"})
            assert resp.status_code == 200
            data = resp.json()
            assert "assets" in data
            symbols = [a["symbol"] for a in data["assets"]]
            assert "AAPL" in symbols
            assert all(s.startswith("AAPL") for s in symbols)
    finally:
        assets_module._assets_cache = []
        assets_module._assets_loaded = False


@pytest.mark.asyncio
async def test_asset_search_empty_query():
    assets_module._assets_cache = [
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "asset_type": "stock"},
    ]
    assets_module._assets_loaded = True
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/assets/search", params={"q": ""})
            assert resp.status_code == 200
            data = resp.json()
            assert data["assets"] == []
    finally:
        assets_module._assets_cache = []
        assets_module._assets_loaded = False
