from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..middleware.tier_gating import get_user_tier
from ..schemas.analysis import AnalysisRequest
from ..services.alpaca_client import AlpacaClient
from ..services.indicator_engine import create_pro_engine
from ..services.verdicts import aggregate
from ..services.ai_analyzer import build_prompt, analyze as llm_analyze
from ..services.news_service import get_news_sentiment
from ..services.cache import cache_get, cache_set, ai_key, CACHE_TTL_AI
from ..config import settings
import logging

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger("vexarium.api.ai")


async def _ai_access(request: Request, body: AnalysisRequest):
    """Allow Pro users always; allow free users for featured preview symbols."""
    token = request.query_params.get("token", "")
    user_tier = await get_user_tier(token)
    if user_tier == "pro":
        return {"tier": "pro", "is_preview": False}
    # Free user -> only allowed for featured symbols (Pro preview).
    sym = validate_symbol(body.symbol).upper()
    if sym in settings.featured_symbol_list:
        return {"tier": "free", "is_preview": True}
    raise HTTPException(status_code=403, detail="Requires pro tier. Upgrade to access AI analysis.")


def _fetch_news(client: AlpacaClient, symbol: str) -> tuple[dict, list]:
    """Fetch recent news. Returns (sentiment_summary, article_list). Never raises."""
    try:
        articles = client.get_news(symbol, limit=10)
        return get_news_sentiment(articles), articles
    except Exception:
        logger.error("News fetch failed for %s", symbol, exc_info=True)
        return (
            {"sentiment_score": 0.0, "article_count": 0, "summary": "News unavailable."},
            [],
        )


@router.post("/ai")
@limiter.limit(f"{settings.rate_limit_pro}/minute")
async def ai_analysis(request: Request, body: AnalysisRequest, access: dict = Depends(_ai_access)):
    sym = validate_symbol(body.symbol)
    is_preview = access.get("is_preview", False)
    try:
        cached = await cache_get(ai_key(sym))
        if cached:
            if is_preview:
                cached["is_preview"] = True
            return cached

        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            return {"symbol": sym, "analysis": "No data available.", "model": settings.llm_model}
        engine = create_pro_engine()
        indicator_results = [r.to_dict() for r in engine.compute_all(df)]
        overall = aggregate(indicator_results)
        news, articles = _fetch_news(client, sym)
        # Comprehensive context: live price, day change, 52-week range, YTD change.
        market_data = client.get_market_snapshot(sym, df)
        prompt = build_prompt(
            indicator_results, overall,
            news_sentiment=news, news_articles=articles,
            market_data=market_data,
        )
        text = await llm_analyze(prompt)
        result = {
            "symbol": sym,
            "analysis": text,
            "model": settings.llm_model,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "news_sentiment": news,
            "news_articles": articles[:8],
            "market": market_data,
            "is_preview": is_preview,
        }
        await cache_set(ai_key(sym), result, ttl=CACHE_TTL_AI)
        return result
    except Exception:
        logger.error("AI analysis failed", exc_info=True)
        return {"symbol": sym, "analysis": "AI analysis temporarily unavailable.", "model": settings.llm_model}
