"""CNN Fear & Greed index — market-wide sentiment gauge.

CNN has no public API; the number its page shows comes from
`production.dataviz.cnn.io/index/fearandgreed/graphdata`. Plain requests get
HTTP 418'd, so we mimic a browser: first load the CNN markets page to collect
cookies, then call the dataviz endpoint with those cookies + a browser UA +
Referer. Cached 30 min (CNN updates it through US market hours).

Unofficial endpoint — can change/break; every path degrades to None so the
widget shows "unavailable" instead of erroring.
"""
from __future__ import annotations

import logging
import math

import httpx

from .cache import CACHE_TTL_NEWS, cache_get, cache_set, run_coro

logger = logging.getLogger("vexarium.fear_greed")

_FEAR_GREED_PAGE = "https://edition.cnn.com/markets/fear-and-greed"
_FEAR_GREED_JSON = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_TIMEOUT = 15.0


def rating(score: float) -> str:
    if score <= 25:
        return "extreme fear"
    if score < 45:
        return "fear"
    if score <= 55:
        return "neutral"
    if score < 75:
        return "greed"
    return "extreme greed"


def parse(data: dict) -> dict | None:
    """Extract the current snapshot from the CNN dataviz payload."""
    fg = data.get("fear_and_greed") or {}
    raw = fg.get("score")
    if raw is None:
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    score = round(score, 1)
    return {
        "score": score,
        "rating": rating(score),
        "timestamp": fg.get("timestamp") or "",
        "previous_close": fg.get("previous_close"),
        "previous_1_week": fg.get("previous_1_week"),
        "previous_1_month": fg.get("previous_1_month"),
    }


def get_fear_greed() -> dict | None:
    """Current CNN Fear & Greed snapshot, cached 30 min. None on any failure."""
    cached = run_coro(cache_get("fear-greed"))
    if cached is not None:
        return cached
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            # 1) acquire cookies from the page (bot-block handshake)
            client.get(_FEAR_GREED_PAGE, headers={"User-Agent": _UA})
            # 2) fetch the JSON the page itself consumes
            resp = client.get(
                _FEAR_GREED_JSON,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json",
                    "Referer": _FEAR_GREED_PAGE,
                },
            )
            resp.raise_for_status()
            parsed = parse(resp.json())
    except Exception:
        logger.warning("Fear & Greed fetch failed", exc_info=True)
        return None
    if parsed is None:
        return None
    run_coro(cache_set("fear-greed", parsed, ttl=CACHE_TTL_NEWS))
    return parsed
