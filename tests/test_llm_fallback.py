"""Free-model fallback chain for the LLM analysis endpoint."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import ai_analyzer
from app.services.ai_analyzer import _model_chain, analyze


def test_model_chain_primary_first_deduped():
    with patch.object(ai_analyzer.settings, "llm_model", "deepseek-v4-flash-free"), patch.object(
        ai_analyzer.settings,
        "llm_fallback_models",
        "big-pickle,mimo-v2.5-free,deepseek-v4-flash-free,north-mini-code-free",
    ), patch.object(ai_analyzer.settings, "llm_paid_fallback", "deepseek-v4-flash"):
        chain = _model_chain()
    assert chain[0] == "deepseek-v4-flash-free"
    assert chain == [
        "deepseek-v4-flash-free",
        "big-pickle",
        "mimo-v2.5-free",
        "north-mini-code-free",
        "deepseek-v4-flash",
    ]


@pytest.mark.asyncio
async def test_analyze_falls_back_when_primary_429s():
    """A 429 on the primary model must fall through to the next free model."""
    req = __import__("httpx").Request("POST", "http://llm")
    responses = [
        __import__("httpx").Response(429, json={"error": {"message": "rate limited"}}, request=req),
        __import__("httpx").Response(
            200, json={"choices": [{"message": {"content": "answer from fallback"}}]}, request=req
        ),
    ]
    posts = [AsyncMock(return_value=r) for r in responses]

    class FakeClient:
        def __init__(self, *a, **kw):
            self.post = posts.pop(0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch.object(ai_analyzer.settings, "llm_api_key", "key"), patch.object(
        ai_analyzer.settings, "llm_model", "deepseek-v4-flash-free"
    ), patch.object(
        ai_analyzer.settings, "llm_fallback_models", "big-pickle"
    ), patch.object(ai_analyzer.httpx, "AsyncClient", FakeClient):
        result = await analyze("prompt")

    assert result == "answer from fallback"
