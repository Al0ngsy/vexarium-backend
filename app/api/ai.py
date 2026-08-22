from datetime import datetime, timezone
import asyncio
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
from ..services.news_service import fetch_news
from ..services.company_info import get_company_info
from ..services.cache import (
    cache_get, cache_set, ai_key, ai_lock_key, CACHE_TTL_AI,
    lock_acquire, lock_held, lock_release,
)
from ..config import settings
import logging

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger("vexarium.api.ai")


async def _ai_access(request: Request, body: AnalysisRequest) -> str:
    """AI is free for everyone (IP rate-limited). No token / tier / featured
    gating — the endpoint is open and protected by the per-IP rate limit and
    the 24h per-symbol cache. Tier is still detected (for the Pro-only
    options-strategies endpoint) but never blocks the main AI endpoint."""
    token = request.query_params.get("token", "")
    return await get_user_tier(token)


def _build_context(sym: str, timeframe: str = "1d") -> tuple:
    """Assemble everything the LLM prompt needs for a symbol.

    Returns (df, indicator_results, overall, news, articles, market_data,
    company_info, prompt). Raises on upstream failures the caller must handle.
    """
    client = AlpacaClient()
    df = client.get_stock_bars(sym, timeframe=timeframe)
    if df.empty:
        raise ValueError("No data available.")
    engine = create_pro_engine()
    indicator_results = [r.to_dict() for r in engine.compute_all(df)]
    overall = aggregate(indicator_results)
    news, articles = fetch_news(client, sym)
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
    return df, indicator_results, overall, news, articles, market_data, company_info, prompt


def _make_result(sym: str, text: str, news, articles, market_data) -> dict:
    return {
        "symbol": sym,
        "analysis": text,
        "model": settings.llm_model,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "news_sentiment": news,
        "news_articles": articles[:8],
        "market": market_data,
    }


@router.post("/ai")
@limiter.limit(f"{settings.rate_limit_ai}/minute")
async def ai_analysis(request: Request, body: AnalysisRequest, user_tier: str = Depends(_ai_access)):
    sym = validate_symbol(body.symbol)
    tf = body.timeframe or "1d"

    async def _cached_or_none():
        return await cache_get(ai_key(sym, tf))

    try:
        cached = await _cached_or_none()
        if cached:
            return cached

        # Single-flight: only ONE LLM call per symbol at a time. If another
        # request (same user refreshing, or a different user) is already
        # generating this symbol, wait for its result instead of firing a
        # duplicate 50-90s LLM call.
        lock_key = ai_lock_key(sym, tf)
        if not await lock_acquire(lock_key, ttl=180):
            # Someone else is generating — poll for the result (up to ~2 min).
            waited = 0
            while waited < 120:
                await asyncio.sleep(2)
                waited += 2
                cached = await _cached_or_none()
                if cached:
                    return cached
                if not await lock_held(lock_key):
                    # Lock released without a cached result -> the generator
                    # failed; don't keep waiting, try once ourselves.
                    break
            if not cached:
                # Lock is free now (or the wait timed out): take over.
                if not await lock_acquire(lock_key, ttl=180):
                    return {"symbol": sym, "analysis": "AI analysis in progress. Please retry in a moment.", "model": settings.llm_model}

        _, _, _, news, articles, market_data, _, prompt = _build_context(sym, tf)
        text = await llm_analyze(prompt)
        # Never cache failure text: a transient LLM outage must not poison the
        # 24h per-symbol cache (the fallback would be served to every user all
        # day even after the provider recovers).
        if text.startswith("AI analysis"):
            return {"symbol": sym, "analysis": text, "model": settings.llm_model}
        # Completeness guard: only cache answers that look finished. The model
        # is instructed to end with the disclaimer footer; a truncated response
        # (max_tokens hit / interrupted stream) won't contain it.
        if "not financial advice" not in text.lower():
            logger.warning("AI answer for %s missing disclaimer footer — not caching (%d chars)", sym, len(text))
            return _make_result(sym, text, news, articles, market_data)
        result = _make_result(sym, text, news, articles, market_data)
        await cache_set(ai_key(sym, tf), result, ttl=CACHE_TTL_AI)
        return result
    except ValueError as e:
        return {"symbol": sym, "analysis": str(e), "model": settings.llm_model}
    except Exception:
        logger.error("AI analysis failed", exc_info=True)
        return {"symbol": sym, "analysis": "AI analysis temporarily unavailable.", "model": settings.llm_model}
    finally:
        await lock_release(ai_lock_key(sym, tf))


@router.post("/ai/stream")
@limiter.limit(f"{settings.rate_limit_ai}/minute")
async def ai_analysis_stream(request: Request, body: AnalysisRequest, user_tier: str = Depends(_ai_access)):
    """SSE streaming AI analysis.

    - Uncached: streams live tokens from the LLM as they arrive (the answer
      appears progressively), then caches the full result for 24h.
    - Cached: replays the stored answer in small chunks with tiny delays — the
      illusion of streaming, so the UI behaves identically either way.

    Events: `data: {"chunk": "..."}\\n\\n` ... then `data: {"done": true}\\n\\n`.
    """
    from fastapi.responses import StreamingResponse
    from ..services.ai_analyzer import analyze_stream

    sym = validate_symbol(body.symbol)
    tf = body.timeframe or "1d"

    async def _cached_or_none():
        return await cache_get(ai_key(sym, tf))

    async def _stream_text(text: str, chunk_size: int = 24, delay: float = 0.03):
        """Replay stored text in small chunks (illusion of live generation)."""
        for i in range(0, len(text), chunk_size):
            yield "data: " + json.dumps({"chunk": text[i:i + chunk_size]}) + "\n\n"
            await asyncio.sleep(delay)

    async def event_generator():
        try:
            cached = await _cached_or_none()
            if cached:
                async for ev in _stream_text(cached["analysis"]):
                    yield ev
                yield "data: " + json.dumps({"done": True}) + "\n\n"
                return

            # Single-flight (same as the non-streaming endpoint).
            lock_key = ai_lock_key(sym, tf)
            acquired = await lock_acquire(lock_key, ttl=180)
            if not acquired:
                waited = 0
                while waited < 120:
                    await asyncio.sleep(2)
                    waited += 2
                    cached = await _cached_or_none()
                    if cached:
                        async for ev in _stream_text(cached["analysis"]):
                            yield ev
                        yield "data: " + json.dumps({"done": True}) + "\n\n"
                        return
                    if not await lock_held(lock_key):
                        break
                if not await lock_acquire(lock_key, ttl=180):
                    yield "data: " + json.dumps({"chunk": "AI analysis in progress. Please retry in a moment."}) + "\n\n"
                    yield "data: " + json.dumps({"done": True}) + "\n\n"
                    return

            _, _, _, news, articles, market_data, _, prompt = _build_context(sym, tf)
            parts = []
            try:
                async for token in analyze_stream(prompt):
                    parts.append(token)
                    yield "data: " + json.dumps({"chunk": token}) + "\n\n"
            except Exception:
                # Mid-stream failure (LLM error / disconnect): show what
                # arrived, but NEVER cache the partial text — a truncated
                # briefing would be served to every user for 24h.
                logger.error("AI stream interrupted for %s", sym, exc_info=True)
                yield "data: " + json.dumps({"done": True}) + "\n\n"
                return
            text = "".join(parts)
            if not text or text.startswith("AI analysis"):
                yield "data: " + json.dumps({"chunk": "AI analysis unavailable. Review the technical indicators manually or come back later."}) + "\n\n"
                yield "data: " + json.dumps({"done": True}) + "\n\n"
                return
            # Completeness guard: only cache answers that look finished. The
            # model is instructed to end with the disclaimer footer; a stream
            # that stops mid-answer (even without raising) won't contain it.
            if "not financial advice" not in text.lower():
                logger.warning("AI answer for %s missing disclaimer footer — not caching (%d chars)", sym, len(text))
                yield "data: " + json.dumps({"done": True}) + "\n\n"
                return
            result = _make_result(sym, text, news, articles, market_data)
            await cache_set(ai_key(sym, tf), result, ttl=CACHE_TTL_AI)
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        except ValueError as e:
            yield "data: " + json.dumps({"chunk": str(e)}) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        except Exception:
            logger.error("AI streaming failed", exc_info=True)
            yield "data: " + json.dumps({"chunk": "AI analysis unavailable. Review the technical indicators manually or come back later."}) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        finally:
            await lock_release(ai_lock_key(sym, tf))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/options-strategies")
@limiter.limit(f"{settings.rate_limit_pro}/minute")
async def ai_options_strategies(request: Request, body: AnalysisRequest, user_tier: str = Depends(_ai_access)):
    """Pro feature: natural-language explanation of recommended options strategies.

    Feeds the technical indicators + a user's chosen strike into the strategy
    engine, then has the LLM explain which strategy fits and why — a
    decision-impacting interpretation. Pro-gated (no free preview here).
    """
    sym = validate_symbol(body.symbol)
    strike = body.strike or 0.0
    # DEV: Pro gate removed during development; re-add before launch:
    #   if user_tier != "pro":
    #       raise HTTPException(status_code=403, detail="Requires pro tier. Upgrade to access options AI.")
    try:
        client = AlpacaClient()
        from ..services.strategy_engine import recommend_strategies
        from datetime import date, timedelta
        gte = date.today().isoformat()
        lte = (date.today() + timedelta(days=60)).isoformat()
        contracts = client.get_option_chain(sym, expiration_gte=gte, expiration_lte=lte)
        chain = []
        for c in contracts:
            raw_type = str(c.get('type', 'call'))
            if '.' in raw_type:
                raw_type = raw_type.rsplit('.', 1)[-1]
            raw_type = raw_type.lower()
            if raw_type not in ('call', 'put'):
                continue
            bid = float(c.get('bid', 0) or 0)
            ask = float(c.get('ask', 0) or 0)
            last = float(c.get('last_price', 0) or 0)
            mid = ((bid + ask) / 2) if (bid and ask) else (last or bid or ask)
            chain.append({
                'strike_price': float(c.get('strike_price', 0)),
                'type': raw_type,
                'last_price': mid,
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
        }
    except Exception:
        logger.error("Options-strategies AI failed", exc_info=True)
        return {"symbol": sym, "analysis": "Options AI temporarily unavailable.", "strategies": [], "model": settings.llm_model}
