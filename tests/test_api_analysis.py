import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, MagicMock
import pandas as pd
from app.main import app


def make_synthetic_df(n=250):
    import numpy as np
    np.random.seed(42)
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": closes - 0.5, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": [1000000.0]*n,
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
    })


@pytest.mark.asyncio
async def test_analysis_returns_verdict():
    df = make_synthetic_df()
    with patch("app.api.analysis.AlpacaClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/analysis", json={"symbol": "AAPL", "asset_type": "stock"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["symbol"] == "AAPL"
            assert "overall" in data
            assert "overall_verdict" in data["overall"]
            assert "indicators" in data
            assert len(data["indicators"]) > 0
            for ind in data["indicators"]:
                assert ind["verdict"] in ("strong_buy", "buy", "hold", "sell", "strong_sell")


@pytest.mark.asyncio
async def test_analysis_not_found():
    with patch("app.api.analysis.AlpacaClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = pd.DataFrame()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/analysis", json={"symbol": "BADSYMBOL"})
            assert resp.status_code == 404
