import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_winning_trade_take_profit():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/portfolio/stance", json={
            "symbol": "AAPL", "entry_price": 100.0, "current_price": 115.0, "trade_type": "stock"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stance"] == "TAKE_PROFIT"
        assert data["pnl_pct"] == 0.15

@pytest.mark.asyncio
async def test_losing_trade_cut_loss():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/portfolio/stance", json={
            "symbol": "AAPL", "entry_price": 100.0, "current_price": 90.0, "trade_type": "stock"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stance"] == "CUT_LOSS"
        assert data["pnl_pct"] == -0.1

@pytest.mark.asyncio
async def test_normal_trade_hold():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/portfolio/stance", json={
            "symbol": "AAPL", "entry_price": 100.0, "current_price": 103.0, "trade_type": "stock"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stance"] == "HOLD"

@pytest.mark.asyncio
async def test_near_expiry_option_take_profit():
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=3)).isoformat()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/portfolio/stance", json={
            "symbol": "AAPL", "entry_price": 100.0, "current_price": 103.0,
            "trade_type": "option", "contract": {"expiration_date": expiry}
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stance"] == "TAKE_PROFIT"
