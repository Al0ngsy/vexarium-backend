from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..middleware.tier_gating import require_tier
from ..schemas.analysis import AnalysisRequest
from ..services.alpaca_client import AlpacaClient
from ..services.indicator_engine import create_pro_engine
from ..services.verdicts import aggregate
from ..services.ai_analyzer import build_prompt, analyze as llm_analyze
from ..services.news_service import get_news_sentiment
from ..config import settings
import logging

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger("vexarium.api.ai")


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
async def ai_analysis(request: Request, body: AnalysisRequest, _: str = Depends(require_tier("pro"))):
    sym = validate_symbol(body.symbol)
    try:
        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            return {"symbol": sym, "analysis": "No data available.", "model": settings.llm_model}
        engine = create_pro_engine()
        indicator_results = [r.to_dict() for r in engine.compute_all(df)]
        overall = aggregate(indicator_results)
        news, articles = _fetch_news(client, sym)
        prompt = build_prompt(indicator_results, overall, news_sentiment=news, news_articles=articles)
        text = await llm_analyze(prompt)
        return {
            "symbol": sym,
            "analysis": text,
            "model": settings.llm_model,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "news_sentiment": news,
            "news_articles": articles[:8],
        }
    except Exception:
        logger.error("AI analysis failed", exc_info=True)
        return {"symbol": sym, "analysis": "AI analysis temporarily unavailable.", "model": settings.llm_model}
