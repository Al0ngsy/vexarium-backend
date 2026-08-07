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
        with patch("app.api.assets._yahoo_search", return_value=[]):
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
        with patch("app.api.assets._yahoo_search", return_value=[]):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/assets/search", params={"q": ""})
                assert resp.status_code == 200
                data = resp.json()
                assert data["assets"] == []
    finally:
        assets_module._assets_cache = []
        assets_module._assets_loaded = False


@pytest.mark.asyncio
async def test_asset_search_merges_yahoo_main_listing():
    """Company-name search surfaces the main listing (RHM.DE / XETRA) from
    Yahoo even though Alpaca's US universe only has the OTC ADR (RNMBY)."""
    assets_module._assets_cache = [
        {"symbol": "RNMBY", "name": "Rheinmetall Ag Unsponsored ADR", "exchange": "OTC", "asset_type": "stock"},
    ]
    assets_module._assets_loaded = True
    yahoo_results = [
        {"symbol": "RHM.DE", "name": "Rheinmetall AG", "exchange": "XETRA", "asset_type": "stock"},
        {"symbol": "RHM.F", "name": "Rheinmetall AG", "exchange": "Frankfurt", "asset_type": "stock"},
    ]
    try:
        with patch("app.api.assets._yahoo_search", return_value=yahoo_results):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/assets/search", params={"q": "Rheinmetall"})
                assert resp.status_code == 200
                symbols = [a["symbol"] for a in resp.json()["assets"]]
                # Main listing first (Yahoo order), then the Alpaca ADR.
                assert symbols[0] == "RHM.DE"
                assert "RNMBY" in symbols
    finally:
        assets_module._assets_cache = []
        assets_module._assets_loaded = False


@pytest.mark.asyncio
async def test_asset_search_wkn_fallback():
    """German WKNs (A1JX52, ETF146, …) resolve via wallstreet-online name
    lookup -> Yahoo name search, German listings ranked first."""
    assets_module._assets_cache = []
    assets_module._assets_loaded = True
    wkn_results = [
        {"symbol": "VWRD.L", "name": "Vanguard FTSE All-World UCITS ETF", "exchange": "London", "exch_code": "LSE", "asset_type": "etf"},
        {"symbol": "VGWL.DE", "name": "Vanguard FTSE All-World U.ETF R", "exchange": "XETRA", "exch_code": "GER", "asset_type": "etf"},
    ]
    try:
        with (
            patch("app.api.assets._yahoo_search", return_value=[]),
            patch("app.api.assets._wso_fund_name", return_value="Vanguard FTSE All-World UCITS ETF Distributing"),
            patch("app.api.assets._yahoo_search_quotes", return_value=wkn_results),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/assets/search", params={"q": "A1JX52"})
                assert resp.status_code == 200
                symbols = [a["symbol"] for a in resp.json()["assets"]]
                # XETRA listing ranks above the LSE one.
                assert symbols == ["VGWL.DE", "VWRD.L"]
    finally:
        assets_module._assets_cache = []
        assets_module._assets_loaded = False
