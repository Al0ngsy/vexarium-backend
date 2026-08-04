import pytest
import json
from app.services.news_service import compute_sentiment, get_news_sentiment
from app.services.ai_analyzer import build_prompt, analyze

def test_sentiment_positive():
    assert compute_sentiment("stock surges on record profit growth") > 0

def test_sentiment_negative():
    assert compute_sentiment("company warns of decline and loss") < 0

def test_sentiment_neutral():
    assert compute_sentiment("company announces quarterly results") == 0.0

def test_sentiment_empty():
    assert compute_sentiment("") == 0.0

def test_get_news_sentiment_with_articles():
    articles = [
        {"headline": "AAPL surges on record profit"},
        {"headline": "AAPL warns of decline"},
    ]
    result = get_news_sentiment(articles)
    assert "sentiment_score" in result
    assert result["article_count"] == 2
    assert "summary" in result

def test_get_news_sentiment_empty():
    result = get_news_sentiment([])
    assert result["sentiment_score"] == 0.0
    assert result["article_count"] == 0

def test_build_prompt_contains_indicators():
    indicators = [
        {"name": "RSI", "verdict": "strong_buy", "value": 28.5},
        {"name": "MACD", "verdict": "buy", "value": 0.5},
    ]
    overall = {"overall_verdict": "buy", "score": 3, "indicator_count": 2, "breakdown": []}
    prompt = build_prompt(indicators, overall)
    assert "RSI" in prompt
    assert "strong_buy" in prompt
    assert "buy" in prompt
    assert "not financial advice" in prompt.lower()

def test_build_prompt_with_options():
    indicators = [{"name": "RSI", "verdict": "hold", "value": 50}]
    overall = {"overall_verdict": "hold", "score": 0, "indicator_count": 1, "breakdown": []}
    options = {"greeks": {"delta": 0.5, "theta": -0.05}}
    prompt = build_prompt(indicators, overall, options_data=options)
    assert "delta" in prompt

def test_build_prompt_with_news():
    indicators = [{"name": "RSI", "verdict": "buy", "value": 35}]
    overall = {"overall_verdict": "buy", "score": 1, "indicator_count": 1, "breakdown": []}
    news = {"sentiment_score": 0.5, "summary": "Positive news."}
    prompt = build_prompt(indicators, overall, news_sentiment=news)
    assert "sentiment_score" in prompt

def test_build_prompt_with_news_articles():
    indicators = [{"name": "RSI", "verdict": "buy", "value": 35}]
    overall = {"overall_verdict": "buy", "score": 1, "indicator_count": 1, "breakdown": []}
    news = {"sentiment_score": 0.5, "summary": "Positive news."}
    articles = [
        {"headline": "AAPL beats earnings, surges", "source": "Reuters", "url": "http://x/1", "summary": "Record quarter."},
        {"headline": "AAPL launches new chip", "source": "Bloomberg", "url": "http://x/2", "summary": "Supply chain."},
    ]
    prompt = build_prompt(indicators, overall, news_sentiment=news, news_articles=articles)
    assert "news_articles" in prompt
    assert "AAPL beats earnings, surges" in prompt
    assert "Reuters" in prompt
    assert "Record quarter." in prompt
    # More than 8 articles are capped.
    many = [{"headline": f"h{i}", "source": "s", "url": "u", "summary": "m"} for i in range(12)]
    prompt2 = build_prompt(indicators, overall, news_sentiment=news, news_articles=many)
    assert prompt2.count('"headline"') == 8  # capped at 8

@pytest.mark.asyncio
async def test_analyze_skip_ai():
    result = await analyze("test prompt", skip_ai=True)
    assert "not financial advice" in result.lower()

@pytest.mark.asyncio
async def test_analyze_no_api_key():
    # Force an empty key so the fallback path is deterministic regardless of
    # whether a real LLM key is present in the environment.
    from unittest.mock import patch
    import app.services.ai_analyzer as analyzer_module
    with patch.object(analyzer_module.settings, "llm_api_key", ""):
        result = await analyze("test prompt", skip_ai=False)
        assert "not financial advice" in result.lower()


# --- AI endpoint tests -----------------------------------------------------

import pandas as pd


def _make_df(n=250):
    import numpy as np
    np.random.seed(7)
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": closes - 0.5, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": [1000000.0] * n,
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
    })


@pytest.mark.asyncio
async def test_ai_endpoint_returns_analysis():
    from unittest.mock import patch
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    df = _make_df()
    from unittest.mock import AsyncMock
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze", new=AsyncMock(return_value="Mocked AI analysis. This is not financial advice.")
    ) as mock_llm:
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = [
            {"headline": "AAPL beats earnings, surges", "source": "Reuters", "url": "http://x/1", "summary": "Record quarter."}
        ]
        mock_instance.get_market_snapshot.return_value = {
            "price": 300.0, "day_change_pct": 1.2, "bid": 299.5, "ask": 300.5,
            "prev_close": 296.5, "high_52w": 320.0, "low_52w": 210.0, "ytd_change_pct": 18.5,
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # AI is gated behind Pro tier: register a user and grant Pro.
            from app.repositories.users import get_user_store
            from app.services.auth import hash_password, create_access_token
            store = get_user_store()
            user = await store.create("pro@test.dev", hash_password("secret123"), tier="pro")
            token = create_access_token(user["id"], tier="pro")
            resp = await client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"}, params={"token": token})
            assert resp.status_code == 200
            data = resp.json()
            assert "analysis" in data
            assert data["symbol"] == "AAPL"
            assert mock_llm.called
            # News articles are passed into the LLM prompt.
            prompt_arg = mock_llm.call_args.args[0] if mock_llm.call_args.args else mock_llm.call_args.kwargs.get("prompt", "")
            assert "news_articles" in prompt_arg
            assert "AAPL beats earnings, surges" in prompt_arg
            # Market context is passed into the LLM prompt.
            assert '"market"' in prompt_arg
            assert "high_52w" in prompt_arg and "ytd_change_pct" in prompt_arg
            # Response surfaces the market context too.
            assert data["market"]["high_52w"] == 320.0
            assert data["market"]["ytd_change_pct"] == 18.5


@pytest.mark.asyncio
async def test_ai_endpoint_rejects_free_tier():
    """AI analysis is a Pro feature — a free (anonymous) user gets 403."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"})
        assert resp.status_code == 403
        assert "Upgrade" in resp.json()["detail"]

