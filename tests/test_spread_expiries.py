"""Tests for _spread_expiries (even sampling across the expiry range)."""
from app.services.alpaca_client import AlpacaClient


def test_spread_picks_evenly_across_range():
    exps = [f"2026-{m:02d}-15" for m in range(1, 13)]  # 12 monthly expiries
    picked = AlpacaClient._spread_expiries(exps, 6)
    # Covers the earliest and latest (LEAPS) expiries.
    assert exps[0] in picked and exps[-1] in picked
    assert len(picked) == 6
    # Roughly evenly spaced (not all clustered at the front).
    assert picked[0] == exps[0]
    assert picked[-1] == exps[-1]


def test_spread_smaller_than_n_returns_all():
    exps = ["2026-08-05", "2026-08-06"]
    assert AlpacaClient._spread_expiries(exps, 10) == exps


def test_spread_no_duplicates():
    exps = [f"2026-{d:02d}" for d in range(1, 31)]
    picked = AlpacaClient._spread_expiries(exps, 7)
    assert len(picked) == len(set(picked)) == 7


def test_spread_empty():
    assert AlpacaClient._spread_expiries([], 5) == []
