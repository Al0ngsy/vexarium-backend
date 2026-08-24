from fastapi import APIRouter, HTTPException, Request, Depends

from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

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
    CompanyInfo,
)
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_pro_engine
from ..services.chart_series import build_price_series, compute_series_for, indicator_kind
from ..services.verdicts import aggregate
from ..services.news_service import fetch_news, add_article_scores, get_news_sentiment
from ..services.company_info import get_company_info
from ..services.finnhub import get_finnhub_bundle, get_market_news
from ..services.fear_greed import get_fear_greed
from ..services.cache import (
    cache_get, cache_set, analysis_key, analysis_lock_key, CACHE_TTL_ANALYSIS,
    lock_acquire, lock_held, lock_release,
)
import asyncio
from ..config import settings
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid

router = APIRouter(prefix="/analysis", tags=["analysis"])

logger = get_logger("analysis")


def _build_response(sym: str, body: AnalysisRequest, df, indicator_results):
    """Compute the full AnalysisResponse payload (indicators, series, news)."""
    client = AlpacaClient()
    overall = aggregate(indicator_results)
    current_price = float(df.iloc[-1]["close"]) if not df.empty else None
    day_change_pct = None
    ytd_change_pct = None
    try:
        snap = client.get_market_snapshot(sym, df)
        day_change_pct = snap.get("day_change_pct")
        ytd_change_pct = snap.get("ytd_change_pct")
    except Exception:
        logger.warning("day_change unavailable for %s", sym, exc_info=True)
    price_series, indicator_series = _build_series_payload(df, indicator_results)
    news, news_articles = fetch_news(client, sym)
    # Free, keyless company/ETF profile (Yahoo meta + Wikipedia summary). The
    # whole analysis is cached 24h, so this only runs once per symbol per day.
    company_info = None
    try:
        raw = get_company_info(sym)
        company_info = CompanyInfo(**{k: v for k, v in raw.items() if k in CompanyInfo.model_fields})
    except Exception:
        logger.error("Company info failed for %s", sym, exc_info=True)
        company_info = None
    return AnalysisResponse(
        symbol=sym,
        asset_type=body.asset_type,
        timeframe=body.timeframe,
        current_price=current_price,
        day_change_pct=day_change_pct,
        ytd_change_pct=ytd_change_pct,
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
        company=company_info,
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
                [x.isoformat() if hasattr(x, "isoformat") else str(x) for x in df["timestamp"]],
                series if series else [],
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
    sym = None
    try:
        sym = validate_symbol(body.symbol)
        logger.info("rid=%s analyze symbol=%s tf=%s", _rid(request), sym, body.timeframe or "1d")
        # Daily bars -> computed indicators only change once per day, so the whole
        # analysis result is cached per symbol per day (cheap repeat lookups).
        key = analysis_key(sym, body.timeframe)
        cached = await cache_get(key)
        if cached is not None:
            logger.info("rid=%s analyze symbol=%s cache=hit", _rid(request), sym)
            try:
                return AnalysisResponse.model_validate(cached)
            except Exception:
                pass
        else:
            logger.info("rid=%s analyze symbol=%s cache=miss", _rid(request), sym)

        # Single-flight: the page fires up to 3 duplicate 1d POSTs on load
        # (main + quiet recompute + verdict strip). On a cold cache they all
        # computed concurrently, stalling a 1-CPU instance for 15-20s+. One
        # request computes; the duplicates wait and read the cached result.
        lock_key = analysis_lock_key(sym, body.timeframe)
        if not await lock_acquire(lock_key, ttl=180):
            logger.info("rid=%s analyze symbol=%s lock busy — waiting for in-flight analysis", _rid(request), sym)
            waited = 0
            while waited < 120:
                await asyncio.sleep(2)
                waited += 2
                cached = await cache_get(key)
                if cached is not None:
                    logger.info("rid=%s analyze symbol=%s got in-flight result after %ds wait", _rid(request), sym, waited)
                    try:
                        return AnalysisResponse.model_validate(cached)
                    except Exception:
                        pass
                if not await lock_held(lock_key):
                    break  # holder failed; take over below
            if cached is None:
                if not await lock_acquire(lock_key, ttl=180):
                    raise HTTPException(
                        status_code=503,
                        detail="Analysis in progress, retry in a moment",
                    )
            logger.info("rid=%s analyze symbol=%s took over after %ds lock wait", _rid(request), sym, waited)

        try:
            client = AlpacaClient()
            df = client.get_stock_bars(sym, timeframe=body.timeframe)
            if df.empty:
                logger.warning("rid=%s analyze symbol=%s no data for tf=%s → 404", _rid(request), sym, body.timeframe or "1d")
                raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
            # All indicators are free (no more free/pro indicator split).
            engine = create_pro_engine()
            indicator_results = engine.compute_all(df)
            response = _build_response(sym, body, df, indicator_results)
            await cache_set(key, response.model_dump(mode="json"), ttl=CACHE_TTL_ANALYSIS)
            logger.info(
                "rid=%s analyze symbol=%s done indicators=%d bars=%d cache=stored",
                _rid(request), sym, len(indicator_results), len(df),
            )
            return response
        finally:
            await lock_release(lock_key)
    except AlpacaError as e:
        logger.warning("rid=%s analyze symbol=%s alpaca_error=%s → 502", _rid(request), sym, e)
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("rid=%s analyze symbol=%s FAILED", _rid(request), sym, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/finnhub/{symbol}")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def finnhub_data(request: Request, symbol: str):
    """Insider transactions, earnings history and peers (Finnhub, 12h cache).

    Each list is empty when the FINNHUB_API_KEY is unset or the symbol has
    no data — widgets degrade gracefully, no error.
    """
    sym = None
    try:
        sym = validate_symbol(symbol)
        logger.info("rid=%s finnhub symbol=%s", _rid(request), sym)
        bundle = get_finnhub_bundle(sym)
        logger.debug(
            "rid=%s finnhub symbol=%s insider=%d earnings=%d peers=%d",
            _rid(request), sym,
            len(bundle.get("insider") or []), len(bundle.get("earnings") or []), len(bundle.get("peers") or []),
        )
        return bundle
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.error("rid=%s finnhub symbol=%s FAILED", _rid(request), sym, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/market-news")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def market_news(request: Request, limit: int = 6):
    """Broad market headlines (Finnhub general news) with per-article scores.

    Independent of any symbol; the news widget shows these next to the
    stock-specific feed. Same sentiment scoring as the stock news.
    """
    try:
        logger.info("rid=%s market-news limit=%d", _rid(request), limit)
        articles = get_market_news(limit=max(1, min(limit, 20)))
        scored = add_article_scores(articles)
        if not scored:
            logger.warning("rid=%s market-news empty result", _rid(request))
        else:
            logger.info("rid=%s market-news done articles=%d", _rid(request), len(scored))
        return {
            "sentiment": get_news_sentiment(scored),
            "articles": [NewsArticle.from_article(a) for a in scored],
        }
    except Exception:
        logger.error("rid=%s market-news FAILED", _rid(request), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/fear-greed")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def fear_greed(request: Request):
    """CNN Fear & Greed index (market-wide mood), ~30 min cache.

    Loaded independently of /analysis — it's a fast, symbol-independent
    gauge. {} when the source is unreachable (widget shows unavailable).
    """
    data = await run_in_threadpool(get_fear_greed)
    if data:
        logger.info("rid=%s fear-greed ok value=%s", _rid(request), data.get("value") if isinstance(data, dict) else "-")
    else:
        logger.warning("rid=%s fear-greed empty → unavailable", _rid(request))
    return data or {}


@router.get("/bars/{symbol}", response_model=list[PricePoint])
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def bars(
    request: Request,
    symbol: str,
    timeframe: str = "1d",
    limit: int = 300,
):
    """OHLC bars for the chart at a selectable resolution.

    timeframe: 1m / 5m / 15m / 1h / 1d / 1w / 1mo. limit caps the number of
    points returned (the widget renders the most recent `limit` bars).
    """
    try:
        sym = validate_symbol(symbol)
        logger.info("rid=%s bars symbol=%s tf=%s limit=%d", _rid(request), sym, timeframe, limit)
        client = AlpacaClient()
        df = client.get_stock_bars(sym, timeframe=timeframe)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        series = build_price_series(df, limit=limit)
        logger.debug("rid=%s bars symbol=%s points=%d bars=%d", _rid(request), sym, len(series), len(df))
        return series
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Bars failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")
