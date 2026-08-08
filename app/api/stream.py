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

router = APIRouter(prefix="/stream", tags=["stream"])


@router.get("/quotes")
async def stream_quotes(request: Request, symbols: str = ""):
    """Server-Sent Events feed of live trades/quotes for the given symbols.

    Query: ?symbols=AAPL,MSFT (comma-separated, max 20).
    Each event: data: {"symbol":"AAPL","price":213.44,"size":100,"ts":"..."}
    Heartbeat comment every 20s keeps proxies from idling the connection out.
    """
    try:
        syms = validate_symbols(symbols)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    queue = await quote_stream.subscribe(syms)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {ev.as_sse()}\n\n"
        finally:
            await quote_stream.unsubscribe(syms, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
