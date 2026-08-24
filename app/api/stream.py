"""SSE endpoint for near-real-time quotes (Alpaca IEX relay)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..services.quote_stream import (
    HEARTBEAT_SECS,
    quote_stream,
    validate_symbols,
)
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid

router = APIRouter(prefix="/stream", tags=["stream"])
logger = get_logger("stream")


@router.get("/quotes")
async def stream_quotes(request: Request, symbols: str = ""):
    """Server-Sent Events feed of live trades/quotes for the given symbols.

    Query: ?symbols=AAPL,MSFT (comma-separated, max 20).
    Each event: data: {"symbol":"AAPL","price":213.44,"size":100,"ts":"...","prev_close":211.9}
    Heartbeat comment every 20s keeps proxies from idling the connection out.
    """
    try:
        syms = validate_symbols(symbols)
    except ValueError as e:
        logger.warning("rid=%s quotes invalid symbols=%r → 400", _rid(request), symbols)
        raise HTTPException(status_code=400, detail=str(e))

    queue = await quote_stream.subscribe(syms)
    logger.info("rid=%s quotes SSE opened symbols=%s", _rid(request), ",".join(syms))

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("rid=%s quotes SSE client disconnected", _rid(request))
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECS)
                except asyncio.TimeoutError:
                    logger.debug("rid=%s quotes heartbeat", _rid(request))
                    yield ": ping\n\n"
                    continue
                logger.debug("rid=%s quotes event symbol=%s price=%s", _rid(request), ev.symbol, ev.price)
                yield f"data: {ev.as_sse()}\n\n"
        finally:
            await quote_stream.unsubscribe(syms, queue)
            logger.info("rid=%s quotes SSE closed symbols=%s", _rid(request), ",".join(syms))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
