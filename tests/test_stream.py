"""Tests for the quote stream relay (normalization + validation + SSE endpoint)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.quote_stream import (
    MAX_SYMBOLS,
    QuoteEvent,
    normalize_alpaca_message,
    quote_stream,
    validate_symbols,
)


# ── validate_symbols ─────────────────────────────────────────────────────
def test_validate_symbols_parses_and_uppercases():
    assert validate_symbols("aapl, msft ,SPY") == ["AAPL", "MSFT", "SPY"]


def test_validate_symbols_rejects_empty():
    with pytest.raises(ValueError):
        validate_symbols("")
    with pytest.raises(ValueError):
        validate_symbols(",")


def test_validate_symbols_rejects_too_many():
    with pytest.raises(ValueError):
        validate_symbols(",".join(f"S{i}" for i in range(MAX_SYMBOLS + 1)))


def test_validate_symbols_rejects_garbage():
    with pytest.raises(ValueError):
        validate_symbols("AAPL!;DROP TABLE")


# ── normalize_alpaca_message ─────────────────────────────────────────────
def test_normalize_trade_message():
    ev = normalize_alpaca_message(
        {"T": "t", "S": "AAPL", "p": 213.44, "s": 100, "t": 1750000000}
    )
    assert isinstance(ev, QuoteEvent)
    assert ev.symbol == "AAPL"
    assert ev.price == 213.44
    assert ev.size == 100
    assert ev.ts  # ISO timestamp rendered


def test_normalize_quote_message_uses_midpoint():
    ev = normalize_alpaca_message(
        {"T": "q", "S": "MSFT", "bp": 400.0, "ap": 400.2, "t": 1750000000}
    )
    assert ev is not None
    assert ev.price == 400.1


def test_normalize_ignores_non_trade_quote():
    assert normalize_alpaca_message({"T": "x", "S": "AAPL", "p": 1}) is None
    assert normalize_alpaca_message({"T": "t"}) is None  # no symbol
    assert normalize_alpaca_message({"T": "q", "S": "AAPL"}) is None  # no bp/ap


def test_sse_payload_is_json():
    ev = QuoteEvent("AAPL", 213.44, 100, "2026-08-08T12:00:00+00:00")
    # Whitespace-tolerant key/value check: strip spaces from the payload so
    # it matches whether as_sse uses default or compact json separators.
    assert '"symbol":"AAPL"' in ev.as_sse().replace(" ", "")
    assert ev.as_sse().replace(" ", "").endswith('"prev_close":null}')
    import json

    assert json.loads(ev.as_sse())["price"] == 213.44


# ── SSE endpoint ─────────────────────────────────────────────────────────
client = TestClient(app)


def test_quotes_requires_symbols():
    resp = client.get("/api/v1/stream/quotes")
    assert resp.status_code == 400
    assert "symbols" in resp.json()["detail"]


def test_quotes_rejects_too_many_symbols():
    syms = ",".join(f"S{i}" for i in range(MAX_SYMBOLS + 1))
    resp = client.get(f"/api/v1/stream/quotes?symbols={syms}")
    assert resp.status_code == 400


async def test_quotes_streams_heartbeat_without_data(monkeypatch):
    # With no upstream Alpaca credentials the manager stays in reconnect;
    # the endpoint must still stream heartbeat pings until disconnect.
    # NOTE: starlette 1.4.1's sync TestClient buffers the whole response
    # before client.stream() returns, so an *infinite* SSE stream hangs
    # there. Drive the ASGI app directly and read the first chunk instead.
    import asyncio
    from starlette.requests import Request

    from app.api.stream import stream_quotes

    monkeypatch.setattr("app.api.stream.HEARTBEAT_SECS", 0.2)
    # Keep the upstream Alpaca websocket out of the test (no credentials).
    monkeypatch.setattr(quote_stream, "_ensure_task", lambda: None)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/v1/stream/quotes",
        "raw_path": b"/api/v1/stream/quotes",
        "root_path": "",
        "scheme": "http",
        "query_string": b"symbols=AAPL",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
        "asgi": {"version": "3.0", "spec_version": "2.4"},
    }

    async def receive() -> dict:
        # Suspend forever so the event loop stays cooperative; starlette's
        # is_disconnected() cancels this before it can complete.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}  # pragma: no cover

    events = []

    async def send(message: dict) -> None:
        events.append(message)
        if message["type"] == "http.response.body":
            raise asyncio.CancelledError  # first chunk = heartbeat; stop streaming

    resp = await stream_quotes(Request(scope, receive=receive), symbols="AAPL")
    try:
        await resp(scope, receive, send)
    except asyncio.CancelledError:
        pass

    start = next(m for m in events if m["type"] == "http.response.start")
    assert start["status"] == 200
    headers = dict(start["headers"])
    assert headers[b"content-type"].startswith(b"text/event-stream")
    body = next(m for m in events if m["type"] == "http.response.body")
    assert b": ping" in body["body"]
