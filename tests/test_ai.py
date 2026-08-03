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

@pytest.mark.asyncio
async def test_analyze_skip_ai():
    result = await analyze("test prompt", skip_ai=True)
    assert "not financial advice" in result.lower()

@pytest.mark.asyncio
async def test_analyze_no_api_key():
    result = await analyze("test prompt", skip_ai=False)
    assert "not financial advice" in result.lower()
