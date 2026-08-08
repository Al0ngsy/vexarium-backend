"""Near-real-time quote streaming: one upstream Alpaca IEX WebSocket fanned
out to N SSE clients.

Design:
- `QuoteStreamManager` owns a single asyncio task holding the Alpaca IEX
  WebSocket. Subscribers register per-symbol asyncio.Queues; events are
  fanned out to every queue subscribed to that symbol.
- Reconnect with exponential backoff. The upstream is IEX (free tier) —
  trades are sparse for illiquid names; SIP is a one-line URL change.
- Pure helpers (`validate_symbols`, `normalize_alpaca_message`) are
  unit-tested; the manager itself is exercised via the SSE endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import websockets

from ..config import settings

logger = logging.getLogger("vexarium.services.quote_stream")

# Free tier = IEX feed. SIP upgrade: change to /v2/sip.
ALPACA_STREAM_URL = "wss://stream.data.alpaca.markets/v2/iex"
MAX_SYMBOLS = 20
QUEUE_MAX = 256
HEARTBEAT_SECS = 20


def validate_symbols(raw: str | None) -> list[str]:
    """Parse + validate the ?symbols= query param. Raises ValueError."""
    if not raw:
        raise ValueError("symbols query parameter is required")
    parts = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not parts:
        raise ValueError("symbols query parameter must not be empty")
    if len(parts) > MAX_SYMBOLS:
        raise ValueError(f"at most {MAX_SYMBOLS} symbols allowed")
    for s in parts:
        if not s.isalnum() or len(s) > 12:
            raise ValueError(f"invalid symbol: {s}")
    return parts


@dataclass
class QuoteEvent:
    symbol: str
    price: float
    size: float
    ts: str  # ISO8601

    def as_sse(self) -> str:
        return json.dumps(
            {"symbol": self.symbol, "price": self.price, "size": self.size, "ts": self.ts}
        )


def normalize_alpaca_message(msg: dict[str, Any]) -> QuoteEvent | None:
    """Normalize an Alpaca IEX trade ('t') or quote ('q') message.

    Trade shape: {"T":"t","S":"AAPL","p":213.44,"s":100,"t":<epoch>}
    Quote shape: {"T":"q","S":"AAPL","bp":..,"ap":..,"t":<epoch>}
    """
    t = msg.get("T")
    symbol = msg.get("S")
    if not symbol:
        return None
    if t == "t":
        price = msg.get("p")
        size = msg.get("s") or 0
    elif t == "q":
        # Midpoint between bid/ask — a stable quote reference price.
        bp, ap = msg.get("bp"), msg.get("ap")
        if bp is None or ap is None:
            return None
        price = (bp + ap) / 2
        size = 0
    else:
        return None
    if price is None:
        return None
    raw_ts = msg.get("t")
    ts = ""
    if raw_ts:
        try:
            ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError, TypeError):
            ts = ""
    return QuoteEvent(symbol=symbol, price=float(price), size=float(size), ts=ts)


class QuoteStreamManager:
    """Single upstream connection fanned out to per-symbol subscriber queues."""

    def __init__(
        self,
        url: str = ALPACA_STREAM_URL,
        reconnect_base: float = 1.0,
        reconnect_max: float = 30.0,
    ) -> None:
        self._url = url
        self._reconnect_base = reconnect_base
        self._reconnect_max = reconnect_max
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def subscribe(self, symbols: list[str]) -> asyncio.Queue:
        """Register a subscriber for symbols; returns its event queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            for s in symbols:
                self._subscribers.setdefault(s, set()).add(q)
            self._ensure_task()
        return q

    async def unsubscribe(self, symbols: list[str], q: asyncio.Queue) -> None:
        """Drop a subscriber; if a symbol has no subscribers left it is unsubscribed upstream."""
        async with self._lock:
            for s in symbols:
                subs = self._subscribers.get(s)
                if subs:
                    subs.discard(q)
                    if not subs:
                        self._subscribers.pop(s, None)

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    @property
    def active_symbols(self) -> list[str]:
        return sorted(self._subscribers.keys())

    async def _run(self) -> None:
        backoff = self._reconnect_base
        while True:
            try:
                await self._connect()
                backoff = self._reconnect_base  # reset on healthy connect
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("quote stream down; reconnect in %.0fs", backoff, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max)

    async def _connect(self) -> None:
        async with websockets.connect(self._url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": settings.alpaca_api_key,
                        "secret": settings.alpaca_secret_key,
                    }
                )
            )
            async with self._lock:
                symbols = self.active_symbols
            if symbols:
                await ws.send(
                    json.dumps(
                        {"action": "subscribe", "trades": symbols, "quotes": symbols}
                    )
                )
            async for raw in ws:
                try:
                    msgs = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(msgs, dict):
                    msgs = [msgs]
                for m in msgs:
                    ev = normalize_alpaca_message(m)
                    if ev is None:
                        continue
                    await self._fanout(ev)

    async def _fanout(self, ev: QuoteEvent) -> None:
        subs = self._subscribers.get(ev.symbol)
        if not subs:
            return
        for q in list(subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Drop the oldest event for this subscriber (slow client).
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


# Module-level singleton used by the SSE endpoint.
quote_stream = QuoteStreamManager()
