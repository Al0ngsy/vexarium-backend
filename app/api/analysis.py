from fastapi import APIRouter, HTTPException, Request, Depends

from datetime import datetime, timezone
import logging

from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    IndicatorResult,
    IndicatorSeries,
    IndicatorPoint,
    NewsArticle,
    OverallVerdict,
    PricePoint,
)
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_pro_engine
from ..services.chart_series import build_price_series, compute_series_for, indicator_kind
from ..middleware.tier_gating import require_tier
from ..services.verdicts import aggregate
from ..services.news_service import get_news_sentiment
from ..services.cache import cache_get, cache_set, analysis_key, CACHE_TTL_ANALYSIS
from ..config import settings

router = APIRouter(prefix="/analysis", tags=["analysis"])

logger = logging.getLogger("vexarium.api.analysis")


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


def _build_response(sym: str, body: AnalysisRequest, df, indicator_results, extended: bool = False):
    """Compute the full AnalysisResponse payload (indicators, series, news)."""
    overall = aggregate(indicator_results)
    current_price = float(df.iloc[-1]["close"]) if not df.empty else None
    price_series, indicator_series = _build_series_payload(df, indicator_results)
    client = AlpacaClient()
    news, news_articles = _fetch_news(client, sym)
    return AnalysisResponse(
        symbol=sym,
        asset_type=body.asset_type,
        current_price=current_price,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        overall=OverallVerdict(
            overall_verdict=overall["overall_verdict"],
            score=overall["score"],
            indicator_count=overall["indicator_count"],
            breakdown=[IndicatorResult(**r) for r in overall["breakdown"]],
        ),
        indicators=[IndicatorResult(**r.to_dict()) for r in indicator_results],
        price_series=price_series,
        indicator_series=indicator_series,
        news_sentiment=news,
        news_articles=[NewsArticle.from_article(a) for a in news_articles[:8]],
    )


def _build_series_payload(df, indicator_results):
    """Compute price_series + indicator_series for charting."""
    price_series = build_price_series(df)
    indicator_series = []
    for r in indicator_results:
        series = compute_series_for(df, r.name)
        pts = [
            {"t": ts, "v": v}
            for ts, v in zip(
                [str(x)[:10] for x in df.tail(120)["timestamp"]],
                series[-120:] if series else [],
            )
        ]
        pts = [p for p in pts if p["v"] is not None]
        indicator_series.append(
            IndicatorSeries(
                name=r.name,
                kind=indicator_kind(r.name),
                points=[IndicatorPoint(**p) for p in pts],
            )
        )
    return [PricePoint(**p) for p in price_series], indicator_series


@router.post("", response_model=AnalysisResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def analyze(request: Request, body: AnalysisRequest):
    try:
        sym = validate_symbol(body.symbol)
        # Daily bars -> computed indicators only change once per day, so the whole
        # analysis result is cached per symbol per day (cheap repeat lookups).
        key = analysis_key(sym, extended=False)
        cached = await cache_get(key)
        if cached is not None:
            try:
                return AnalysisResponse.model_validate(cached)
            except Exception:
                pass
        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        # All indicators are free (no more free/pro indicator split).
        engine = create_pro_engine()
        indicator_results = engine.compute_all(df)
        response = _build_response(sym, body, df, indicator_results)
        await cache_set(key, response.model_dump(mode="json"), ttl=CACHE_TTL_ANALYSIS)
        return response
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Analysis failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/extended", response_model=AnalysisResponse)
@limiter.limit(f"{settings.rate_limit_pro}/minute")
async def analyze_extended(request: Request, body: AnalysisRequest, _: str = Depends(require_tier("pro"))):
    try:
        sym = validate_symbol(body.symbol)
        key = analysis_key(sym, extended=True)
        cached = await cache_get(key)
        if cached is not None:
            try:
                return AnalysisResponse.model_validate(cached)
            except Exception:
                pass
        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        engine = create_pro_engine()
        indicator_results = engine.compute_all(df)
        response = _build_response(sym, body, df, indicator_results, extended=True)
        await cache_set(key, response.model_dump(mode="json"), ttl=CACHE_TTL_ANALYSIS)
        return response
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Analysis failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")
