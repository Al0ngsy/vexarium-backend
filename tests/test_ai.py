import pytest
import json
from unittest.mock import AsyncMock, patch
from app.services.news_service import compute_sentiment, get_news_sentiment, dedupe_articles
from app.services.fear_greed import rating, parse
from app.services.ai_analyzer import build_prompt, analyze

def test_sentiment_positive():
    assert compute_sentiment("stock surges on record profit growth") > 0

def test_sentiment_negative():
    assert compute_sentiment("company warns of decline and loss") < 0

def test_sentiment_neutral():
    assert compute_sentiment("company announces quarterly results") == 0.0

def test_sentiment_empty():
    assert compute_sentiment("") == 0.0

def test_sentiment_negation():
    # VADER flips polarity on negation ("not good" <- 0). The old
    # word-counter missed this entirely (neither "not" nor "good" matched).
    assert compute_sentiment("markets not good today") < 0

def test_sentiment_beyond_lexicon():
    # Words the old wordlist missed ("warns", "hacked") now register.
    assert compute_sentiment("US warns Siemens devices can be hacked") < 0

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


# --- per-article dedupe ----------------------------------------------------

def _art(headline: str, url: str, date: str) -> dict:
    return {"headline": headline, "url": url, "created_at": f"{date}T10:00:00+00:00"}


def test_dedupe_exact_headline():
    assert len(dedupe_articles([
        _art("Apple beats expectations", "http://a/1", "2026-08-19"),
        _art("Apple beats expectations", "http://b/2", "2026-08-19"),
    ])) == 1


def test_dedupe_handles_datetime_created_at():
    # Alpaca returns created_at as a datetime object, not a string.
    from datetime import datetime, timezone

    a = {"headline": "Apple beats expectations", "url": "http://a/1",
         "created_at": datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)}
    b = _art("Apple beats expectations", "http://b/2", "2026-08-19")
    assert len(dedupe_articles([a, b])) == 1


def test_dedupe_same_url_case_insensitive():
    assert len(dedupe_articles([
        _art("Headline A", "http://X/1", "2026-08-19"),
        _art("Headline B", "http://x/1", "2026-08-19"),
    ])) == 1


def test_dedupe_similar_same_day_dropped():
    a = _art("Apple stock jumps on strong earnings beat", "http://a", "2026-08-19")
    b = _art("Apple stock jumps on strong earnings beat again", "http://b", "2026-08-19")
    assert len(dedupe_articles([a, b])) == 1


def test_dedupe_similar_different_day_survives():
    a = _art("Apple stock jumps on strong earnings beat", "http://a", "2026-08-19")
    b = _art("Apple stock jumps on strong earnings beat again", "http://b", "2026-08-20")
    assert len(dedupe_articles([a, b])) == 2


def test_dedupe_empty():
    assert dedupe_articles([]) == []


def test_cap_source_limits_any_single_outlet():
    from app.services.news_service import cap_source

    def art(h: str, u: str, src: str) -> dict:
        return {"headline": h, "url": u, "created_at": "2026-08-19T10:00:00+00:00", "source": src}

    arts = [art(f"Benzinga story {i}", f"http://bz/{i}", "Benzinga") for i in range(5)]
    arts += [art("ChartMill note", "http://cm/1", "ChartMill"), art("Reuters wire", "http://re/1", "Reuters")]
    capped = cap_source(arts, max_per_source=2)
    assert len(capped) == 4  # 2× benzinga + chartmill + reuters
    assert sum(1 for a in capped if a["source"] == "Benzinga") == 2


def test_cap_source_groups_case_insensitively():
    from app.services.news_service import cap_source

    def art(h: str, u: str, src: str) -> dict:
        return {"headline": h, "url": u, "created_at": "2026-08-19T10:00:00+00:00", "source": src}

    arts = [
        art("benzinga a", "http://a", "benzinga"),
        art("Benzinga B", "http://b", "Benzinga"),
        art("benzinga c", "http://c", "benzinga"),
        art("Other d", "http://d", "Other"),
    ]
    assert len(cap_source(arts, max_per_source=2)) == 3  # 2 combined benzinga + 1 other


# --- Fear & Greed ----------------------------------------------------------

def test_fear_greed_rating_bands():
    assert rating(10) == "extreme fear"
    assert rating(30) == "fear"
    assert rating(50) == "neutral"
    assert rating(60) == "greed"
    assert rating(90) == "extreme greed"


def test_fear_greed_parse_snapshot():
    d = parse({"fear_and_greed": {
        "score": 56.3142, "timestamp": "2026-08-19T20:07:20+00:00",
        "previous_close": 54.4, "previous_1_week": 62.9, "previous_1_month": 37.2,
    }})
    assert d is not None
    assert d["score"] == 56.3
    assert d["rating"] == "greed"
    assert d["previous_1_week"] == 62.9


def test_fear_greed_parse_rejects_bad_payloads():
    assert parse({}) is None
    assert parse({"fear_and_greed": {}}) is None
    assert parse({"fear_and_greed": {"score": "NaN"}}) is None

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


def test_build_prompt_with_company_fundamentals():
    indicators = [{"name": "RSI", "verdict": "buy", "value": 35}]
    overall = {"overall_verdict": "buy", "score": 1, "indicator_count": 1, "breakdown": []}
    company = {
        "symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology",
        "pe_ratio": 35.7, "profit_margin": 0.25, "revenue_growth": 0.08,
    }
    prompt = build_prompt(indicators, overall, company_info=company)
    assert "company_fundamentals" in prompt
    assert "pe_ratio" in prompt
    assert "Apple Inc." in prompt
    # Empty/zero fields are filtered out; only non-empty values remain.
    company2 = {"symbol": "AAPL", "name": "", "pe_ratio": 0, "sector": "Tech"}
    prompt2 = build_prompt(indicators, overall, company_info=company2)
    assert "company_fundamentals" in prompt2
    assert "pe_ratio" not in prompt2
    assert "Apple Inc." not in prompt2
    # All-empty company -> no fundamentals section at all.
    company3 = {"symbol": "AAPL", "name": "", "pe_ratio": 0, "sector": ""}
    prompt3 = build_prompt(indicators, overall, company_info=company3)
    assert "company_fundamentals" not in prompt3


def test_build_prompt_key_levels():
    from app.services.ai_analyzer import _key_levels
    indicators = [
        {"name": "Bollinger(20,2)", "verdict": "hold", "value": {"lower": 290.0, "upper": 330.0}},
        {"name": "SMA(50)/EMA(200)", "verdict": "buy", "value": {"sma50": 300.0, "ema200": 280.0}},
    ]
    market = {"price": 310.0, "high_52w": 340.0, "low_52w": 250.0}
    levels = _key_levels(indicators, market)
    assert 290.0 in levels["support"]  # bollinger lower
    assert 300.0 in levels["support"]  # sma50 below price
    assert 330.0 in levels["resistance"]  # bollinger upper
    assert 340.0 in levels["resistance"]  # 52w high
    # Nearest 3 supports only — the far 52w low is cut by the top-3 limit.
    assert 250.0 not in levels["support"]
    assert len(levels["support"]) <= 3
    # No price -> no levels.
    assert _key_levels(indicators, {}) == {"support": [], "resistance": []}

@pytest.mark.asyncio
async def test_analyze_skip_ai():
    result = await analyze("test prompt", skip_ai=True)
    assert "AI analysis unavailable" in result


@pytest.mark.asyncio
async def test_analyze_stream_yields_tokens():
    """analyze_stream yields only `content` deltas (not reasoning_content)."""
    import app.services.ai_analyzer as mod

    lines = [
        'data: {"choices":[{"delta":{"content":null,"reasoning_content":"thinking..."}}]}',
        'data: {"choices":[{"delta":{"content":"Hello "}}]}',
        'data: {"choices":[{"delta":{"content":"world"}}]}',
        'data: {"choices":[{"delta":{"content":null,"finish_reason":"stop"}}]}',
        "data: [DONE]",
    ]

    class StubResp:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for l in lines:
                yield l

    class StubStream:
        async def __aenter__(self):
            return StubResp()

        async def __aexit__(self, *a):
            return False

    class StubClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return StubStream()

    orig = mod.httpx
    mod.httpx = type("H", (), {"AsyncClient": StubClient})()
    try:
        parts = []
        async for token in mod.analyze_stream("prompt"):
            parts.append(token)
        # reasoning_content is skipped; only content deltas are streamed.
        assert parts == ["Hello ", "world"]
    finally:
        mod.httpx = orig


@pytest.mark.asyncio
async def test_ai_stream_endpoint_cached_replay(monkeypatch):
    """/ai/stream replays a cached answer chunk-by-chunk (illusion of streaming)."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services import cache as cache_module

    state = {"data": {}}

    class FakeRedis:
        async def set(self, key, value, ex=None, nx=False):
            if nx:
                if key in state["data"]:
                    return False
                state["data"][key] = "1"
                return True
            state["data"][key] = value
            return True

        async def get(self, key):
            return state["data"].get(key)

        async def exists(self, key):
            return 1 if key in state["data"] else 0

        async def delete(self, key):
            state["data"].pop(key, None)

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    # Pre-populate the AI cache for AAPL.
    from app.services.cache import ai_key
    await cache_module.cache_set(
        ai_key("AAPL"),
        {"symbol": "AAPL", "analysis": "## Summary\n**Sell.** Cached answer."},
        ttl=86400,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/analysis/ai/stream", json={"symbol": "AAPL"}
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
    text = body.decode()
    # Multiple chunk events were emitted (the illusion of streaming), framed
    # as proper SSE (data: prefix) so the frontend parser can consume them.
    assert 'data: {"chunk":' in text
    assert 'data: {"done": true}' in text
    assert "## Summary" in text

@pytest.mark.asyncio
async def test_analyze_no_api_key():
    # Force an empty key so the fallback path is deterministic regardless of
    # whether a real LLM key is present in the environment.
    from unittest.mock import patch
    import app.services.ai_analyzer as analyzer_module
    with patch.object(analyzer_module.settings, "llm_api_key", ""):
        result = await analyze("test prompt", skip_ai=False)
        assert "AI analysis unavailable" in result


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
        "app.api.ai.llm_analyze", new=AsyncMock(return_value="Mocked AI analysis. This is not financial advice. AI can make/will make mistakes.")
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
async def test_ai_endpoint_open_to_anonymous():
    """AI is free for everyone now: an anonymous (no-token) request for ANY
    symbol (featured or not) returns 200 — protected by IP rate limit + cache."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/analysis/ai", json={"symbol": "XYZ"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ai_endpoint_any_symbol_free():
    """No more featured-only preview: any symbol works for anonymous users and
    is_preview is always False (no conversion teaser)."""
    from unittest.mock import patch, AsyncMock
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    df = _make_df()
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze", new=AsyncMock(return_value="Mocked AI. Not financial advice.")
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = []
        mock_instance.get_market_snapshot.return_value = {"price": 300.0}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"})
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_options_strategies_ai_pro_only():
    """Options-strategies AI is Pro-only: a free user gets 403."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/analysis/options-strategies", json={"symbol": "AAPL", "strike": 300})
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ai_failure_not_cached(monkeypatch):
    """A transient LLM failure must NOT be cached: the fallback text would
    otherwise poison the 24h per-symbol cache for every user all day."""
    from unittest.mock import patch, AsyncMock
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services import cache as cache_module

    captured = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            captured[key] = value  # record every cache write attempt

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    df = _make_df()
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze",
        new=AsyncMock(return_value="AI analysis unavailable. Review the technical indicators manually or come back later."),
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = []
        mock_instance.get_market_snapshot.return_value = {"price": 300.0}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"})
            assert resp.status_code == 200
            assert "unavailable" in resp.json()["analysis"]
            # The AI failure must NOT have been written to the cache
            # (company:AAPL caching is fine and expected).
            assert not any(k.startswith("ai:") for k in captured)


@pytest.mark.asyncio
async def test_ai_success_cached(monkeypatch):
    """A real analysis IS cached (regression guard for the failure check)."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services import cache as cache_module

    captured = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            captured["key"], captured["value"] = key, value

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    df = _make_df()
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze", new=AsyncMock(return_value="Real analysis. Not financial advice.")
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = []
        mock_instance.get_market_snapshot.return_value = {"price": 300.0}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"})
            assert resp.status_code == 200
            assert captured["key"].startswith("ai:AAPL:")
            assert "Real analysis" in captured["value"]


@pytest.mark.asyncio
async def test_ai_truncated_not_cached(monkeypatch):
    """An answer missing the disclaimer footer is served but NOT cached —
    regression for the INTC bug (a 40-char truncated briefing cached 24h)."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services import cache as cache_module

    captured = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            captured["key"] = key

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    df = _make_df()
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze",
        # Truncated answer — no disclaimer footer (the stream was cut).
        new=AsyncMock(return_value="## Summary\n**Sell.** Intel is below its "),
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = []
        mock_instance.get_market_snapshot.return_value = {"price": 300.0}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/analysis/ai", json={"symbol": "INTC"})
            assert resp.status_code == 200
            # The partial text IS returned (user sees what we have)...
            assert "Intel is below its" in resp.json()["analysis"]
            # ...but the truncated AI answer must NOT have been cached
            # (company:INTC caching is fine and expected).
            assert not any(k.startswith("ai:") for k in captured)


@pytest.mark.asyncio
async def test_ai_stream_interrupted_not_cached(monkeypatch):
    """A mid-stream LLM failure streams what arrived but never caches it —
    the INTC 40-char-cached regression, streaming flavor."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services import cache as cache_module

    captured = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            captured["key"] = key

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    async def interrupted_stream(prompt):
        yield "## Summary\n"
        yield "**Sell.** Intel is below its "
        raise RuntimeError("LLM connection dropped")

    df = _make_df()
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.services.ai_analyzer.analyze_stream", new=interrupted_stream
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = []
        mock_instance.get_market_snapshot.return_value = {"price": 300.0}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST", "/api/v1/analysis/ai/stream", json={"symbol": "INTC"}
            ) as resp:
                assert resp.status_code == 200
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
    text = body.decode()
    # The partial chunks were streamed to the client...
    assert "Intel is below its" in text
    assert 'data: {"done": true}' in text
    # ...but the interrupted answer must NOT have been cached
    # (company:INTC caching is fine and expected).
    assert not any(k.startswith("ai:") for k in captured)


@pytest.mark.asyncio
async def test_ai_single_flight_concurrent(monkeypatch):
    """Two concurrent requests for the same symbol must fire ONE LLM call —
    the second waits for the first and reuses its cached result."""
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services import cache as cache_module

    state = {"lock": None, "data": {}, "llm_calls": 0}

    class FakeRedis:
        async def set(self, key, value, ex=None, nx=False):
            if nx:
                if state["lock"] is not None:
                    return False
                state["lock"] = key
                return True
            state["data"][key] = value
            return True

        async def get(self, key):
            return state["data"].get(key)

        async def exists(self, key):
            return 1 if state["lock"] is not None else 0

        async def delete(self, key):
            state["lock"] = None

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    df = _make_df()

    async def slow_llm(prompt):
        state["llm_calls"] += 1
        await asyncio.sleep(0.3)  # simulate the 50-90s LLM call (scaled down)
        return "Single-flight analysis. Not financial advice."

    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze", new=slow_llm
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_news.return_value = []
        mock_instance.get_market_snapshot.return_value = {"price": 300.0}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Fire both requests concurrently — they race for the same symbol.
            r1, r2 = await asyncio.gather(
                client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"}),
                client.post("/api/v1/analysis/ai", json={"symbol": "AAPL"}),
            )
            assert r1.status_code == 200 and r2.status_code == 200
            d1, d2 = r1.json(), r2.json()
            assert "Single-flight" in d1["analysis"], f"R1 unexpected: {json.dumps(d1)[:200]}"
            assert "Single-flight" in d2["analysis"], f"R2 unexpected: {json.dumps(d2)[:200]}"
            # Only ONE LLM call happened despite two concurrent requests.
            assert state["llm_calls"] == 1
            # The lock was released afterwards.
            assert state["lock"] is None


@pytest.mark.asyncio
async def test_options_strategies_ai_pro_user():
    """A Pro user gets the options-strategies explanation."""
    from unittest.mock import patch, AsyncMock
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.repositories.users import get_user_store
    from app.services.auth import hash_password, create_access_token

    df = _make_df()
    with patch("app.api.ai.AlpacaClient") as MockClient, patch(
        "app.api.ai.llm_analyze", new=AsyncMock(return_value="Consider a bull call spread. Not financial advice.")
    ):
        mock_instance = MockClient.return_value
        mock_instance.get_stock_bars.return_value = df
        mock_instance.get_option_contracts.return_value = []
        store = get_user_store()
        user = await store.create("optspro@test.dev", hash_password("secret123"), tier="pro")
        token = create_access_token(user["id"], tier="pro")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/analysis/options-strategies",
                json={"symbol": "AAPL", "strike": 300},
                params={"token": token},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "analysis" in data and "strategies" in data

