from datetime import datetime, timezone
import json
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
from ..services.company_info import get_company_info
from ..services.cache import cache_get, cache_set, ai_key, CACHE_TTL_AI
from ..config import settings
import logging

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger("vexarium.api.ai")


async def _ai_access(request: Request, body: AnalysisRequest):
    """AI is free for everyone (IP rate-limited). No token / tier / featured
    gating — the endpoint is open and protected by the per-IP rate limit and
    the 24h per-symbol cache. Tier is still detected (for the Pro-only
    options-strategies endpoint) but never blocks the main AI endpoint."""
    token = request.query_params.get("token", "")
    user_tier = await get_user_tier(token)
    return {"tier": user_tier, "is_preview": False}


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
@limiter.limit(f"{settings.rate_limit_ai}/minute")
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
        # Company fundamentals (free keyless Yahoo) — enriches the briefing.
        company_info = None
        try:
            company_info = get_company_info(sym)
        except Exception:
            logger.error("Company info failed for %s", sym, exc_info=True)
        prompt = build_prompt(
            indicator_results, overall,
            news_sentiment=news, news_articles=articles,
            market_data=market_data,
            company_info=company_info,
        )
        text = await llm_analyze(prompt)
        # Never cache failure text: a transient LLM outage must not poison the
        # 24h per-symbol cache (the fallback would be served to every user all
        # day even after the provider recovers).
        if text.startswith("AI analysis"):
            return {"symbol": sym, "analysis": text, "model": settings.llm_model}
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


@router.post("/options-strategies")
@limiter.limit(f"{settings.rate_limit_pro}/minute")
async def ai_options_strategies(request: Request, body: AnalysisRequest, access: dict = Depends(_ai_access)):
    """Pro feature: natural-language explanation of recommended options strategies.

    Feeds the technical indicators + a user's chosen strike into the strategy
    engine, then has the LLM explain which strategy fits and why — a
    decision-impacting interpretation. Pro-gated (no free preview here).
    """
    sym = validate_symbol(body.symbol)
    strike = body.strike or 0.0
    if access.get("tier") != "pro":
        raise HTTPException(status_code=403, detail="Requires pro tier. Upgrade to access options AI.")
    try:
        client = AlpacaClient()
        from ..services.strategy_engine import recommend_strategies
        from datetime import date, timedelta
        gte = date.today().isoformat()
        lte = (date.today() + timedelta(days=60)).isoformat()
        contracts = client.get_option_contracts(sym, gte, lte)
        chain = []
        for c in contracts:
            raw_type = str(c.get('type', 'call'))
            if '.' in raw_type:
                raw_type = raw_type.rsplit('.', 1)[-1]
            raw_type = raw_type.lower()
            if raw_type not in ('call', 'put'):
                continue
            chain.append({
                'strike_price': float(c.get('strike_price', 0)),
                'type': raw_type,
                'last_price': float(c.get('last_price', 0) or 0),
            })
        df = client.get_stock_bars(sym)
        indicator_results = []
        if not df.empty:
            indicator_results = [r.to_dict() for r in create_pro_engine().compute_all(df)]
        from ..services.verdicts import aggregate
        overall = aggregate(indicator_results)
        sentiment = overall["overall_verdict"]
        recs = recommend_strategies(sentiment, strike, chain, indicator_results=indicator_results)
        prompt = (
            f"For {sym}, the technical verdict is {sentiment}. The user is "
            f"considering an option near strike {strike}. Recommended strategies:\n"
            f"{json.dumps(recs, indent=2, default=str)}\n\n"
            "Explain in 2-4 short paragraphs which strategy fits the current "
            "technical picture, its risk/reward, and what the user should watch. "
            "This is educational, not financial advice."
        )
        text = await llm_analyze(prompt)
        return {
            "symbol": sym,
            "strategies": recs,
            "analysis": text,
            "model": settings.llm_model,
            "is_preview": False,
        }
    except Exception:
        logger.error("Options-strategies AI failed", exc_info=True)
        return {"symbol": sym, "analysis": "Options AI temporarily unavailable.", "strategies": [], "model": settings.llm_model}
