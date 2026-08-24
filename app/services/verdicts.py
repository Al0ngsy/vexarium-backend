"""Verdict aggregation for VEXARIUM.

Takes a list of :class:`~app.services.indicator_engine.IndicatorResult`-like
dicts, scores each verdict, sums the scores, and maps the total to an
overall verdict bucket.

Usage::

    from app.services.verdicts import aggregate
    result = aggregate(indicator_results)
    print(result["overall_verdict"])  # "buy"
"""
from __future__ import annotations

from typing import Any

from ..logging import get_logger

logger = get_logger("verdicts")

VERDICT_SCORES: dict[str, int] = {
    "strong_buy": 2,
    "buy": 1,
    "hold": 0,
    "sell": -1,
    "strong_sell": -2,
}


def _score_bucket(total: int) -> str:
    """Map a numeric score total to an overall verdict."""
    if total >= 5:
        return "strong_buy"
    if total >= 2:
        return "buy"
    if total <= -5:
        return "strong_sell"
    if total <= -2:
        return "sell"
    return "hold"  # -1 to 1


def _extract(result: Any) -> tuple[str, str]:
    """Extract (name, verdict) from either a dict or an object."""
    if isinstance(result, dict):
        name = result.get("name", "unknown")
        verdict = result.get("verdict", "hold")
    else:
        name = getattr(result, "name", "unknown")
        verdict = getattr(result, "verdict", "hold")
    return name, verdict


def aggregate(indicator_results: list[Any]) -> dict:
    """Aggregate per-indicator verdicts into an overall verdict.

    Parameters
    ----------
    indicator_results
        List of objects or dicts each having ``name`` and ``verdict`` attributes.
        Results with verdict ``none`` (indicator could not be computed) are
        excluded entirely — from the score, the breakdown, and the count.

    Returns
    -------
    dict with keys: ``overall_verdict``, ``score``, ``breakdown``,
    ``indicator_count``.
    """
    computed = [r for r in indicator_results if _extract(r)[1] != "none"]
    if not computed:
        logger.debug("aggregate all indicators none (verdict=hold score=0)")
        return {
            "overall_verdict": "hold",
            "score": 0,
            "breakdown": [],
            "indicator_count": 0,
        }

    total = 0
    breakdown: list[dict[str, str]] = []
    for result in computed:
        name, verdict = _extract(result)
        score = VERDICT_SCORES.get(verdict, 0)
        total += score
        breakdown.append({"name": name, "verdict": verdict})

    verdict = _score_bucket(total)
    logger.info("aggregate done verdict=%s score=%d indicators=%d excluded_none=%d",
                verdict, total, len(computed), len(indicator_results) - len(computed))
    return {
        "overall_verdict": verdict,
        "score": total,
        "breakdown": breakdown,
        "indicator_count": len(computed),
    }