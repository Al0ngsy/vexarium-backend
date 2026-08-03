"""Tests for verdict aggregation."""
from __future__ import annotations

from app.services.verdicts import VERDICT_SCORES, aggregate


def _result(name: str, verdict: str) -> dict:
    return {"name": name, "verdict": verdict}


# ---------------------------------------------------------------------------
# Score mapping sanity
# ---------------------------------------------------------------------------


def test_verdict_scores():
    assert VERDICT_SCORES["strong_buy"] == 2
    assert VERDICT_SCORES["buy"] == 1
    assert VERDICT_SCORES["hold"] == 0
    assert VERDICT_SCORES["sell"] == -1
    assert VERDICT_SCORES["strong_sell"] == -2


# ---------------------------------------------------------------------------
# Aggregation bucket tests
# ---------------------------------------------------------------------------


def test_all_strong_buy():
    results = [_result(f"ind_{i}", "strong_buy") for i in range(5)]
    out = aggregate(results)
    assert out["overall_verdict"] == "strong_buy"
    assert out["score"] == 10
    assert out["indicator_count"] == 5
    assert len(out["breakdown"]) == 5


def test_all_strong_sell():
    results = [_result(f"ind_{i}", "strong_sell") for i in range(5)]
    out = aggregate(results)
    assert out["overall_verdict"] == "strong_sell"
    assert out["score"] == -10
    assert out["indicator_count"] == 5


def test_mixed_balanced_hold():
    results = [
        _result("a", "strong_buy"),
        _result("b", "buy"),
        _result("c", "hold"),
        _result("d", "sell"),
        _result("e", "strong_sell"),
    ]
    out = aggregate(results)
    # 2 + 1 + 0 - 1 - 2 = 0 → hold
    assert out["score"] == 0
    assert out["overall_verdict"] == "hold"


def test_mixed_buy():
    results = [
        _result("a", "strong_buy"),
        _result("b", "buy"),
        _result("c", "buy"),
    ]
    out = aggregate(results)
    # 2 + 1 + 1 = 4 → buy
    assert out["score"] == 4
    assert out["overall_verdict"] == "buy"


def test_single_indicator():
    out = aggregate([_result("only", "buy")])
    assert out["overall_verdict"] == "hold"  # score 1 → hold
    assert out["score"] == 1
    assert out["indicator_count"] == 1
    assert out["breakdown"] == [{"name": "only", "verdict": "buy"}]


def test_empty_list():
    out = aggregate([])
    assert out["overall_verdict"] == "hold"
    assert out["score"] == 0
    assert out["indicator_count"] == 0
    assert out["breakdown"] == []


def test_single_strong_buy():
    out = aggregate([_result("x", "strong_buy")])
    assert out["score"] == 2
    assert out["overall_verdict"] == "buy"


def test_sell_bucket():
    results = [_result(f"i{i}", "sell") for i in range(3)]
    out = aggregate(results)
    assert out["score"] == -3
    assert out["overall_verdict"] == "sell"


def test_works_with_objects():
    """aggregate should accept objects with .name and .verdict attributes too."""

    class FakeResult:
        def __init__(self, name, verdict):
            self.name = name
            self.verdict = verdict

    results = [FakeResult("a", "strong_buy"), FakeResult("b", "strong_buy"), FakeResult("c", "strong_buy")]
    out = aggregate(results)
    assert out["score"] == 6
    assert out["overall_verdict"] == "strong_buy"
    assert out["breakdown"][0]["name"] == "a"