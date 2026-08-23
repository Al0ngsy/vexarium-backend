import pytest
from app.services.strategy_engine import compute_strategy, build_payoff_curve, recommend_strategies

def test_long_call_metrics():
    s = compute_strategy('long_call', 100, 5, 105)
    assert s['max_loss'] == 5
    assert s['breakeven'] == 105
    assert s['max_profit'] is None
    assert s['name'] == 'LONG CALL'

def test_cash_secured_put_metrics():
    s = compute_strategy('cash_secured_put', 100, 5, 105)
    assert s['max_profit'] == 5
    assert s['breakeven'] == 95
    assert s['max_loss'] == 95

def test_payoff_curve():
    curve = build_payoff_curve(100, 5, 105, True)
    assert len(curve) > 0
    assert all('price' in p and 'pl' in p for p in curve)

def test_recommend_bullish():
    chain = [{'strike_price': 100, 'type': 'call', 'last_price': 5.0}]
    recs = recommend_strategies('bullish', 105, chain)
    assert len(recs) > 0
    assert recs[0]['name'] == 'LONG CALL'


def test_recommend_driven_by_indicators():
    # Indicators say strongly bullish even if the passed sentiment is neutral.
    chain = [{'strike_price': 100, 'type': 'call', 'last_price': 5.0}]
    ind = [
        {'name': 'RSI', 'verdict': 'strong_buy'},
        {'name': 'MACD', 'verdict': 'buy'},
        {'name': 'SMA/EMA', 'verdict': 'buy'},
    ]
    recs = recommend_strategies('neutral', 105, chain, indicator_results=ind)
    assert recs and recs[0]['name'] == 'LONG CALL'


def test_recommend_indicators_neutral_falls_back_to_sentiment():
    # Indicators are neutral -> fall back to the passed sentiment string.
    chain = [{'strike_price': 100, 'type': 'call', 'last_price': 5.0}]
    ind = [{'name': 'RSI', 'verdict': 'hold'}, {'name': 'MACD', 'verdict': 'hold'}]
    recs = recommend_strategies('bearish', 105, chain, indicator_results=ind)
    # bearish with only calls available -> no short_put, falls to neutral branch
    assert isinstance(recs, list)

def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        compute_strategy('bad_strategy', 100, 5, 105)

def test_recommend_bearish_long_put():
    chain = [
        {'strike_price': 100, 'type': 'call', 'last_price': 5.0},
        {'strike_price': 100, 'type': 'put', 'last_price': 4.0},
    ]
    recs = recommend_strategies('bearish', 100, chain)
    names = [r['name'] for r in recs]
    assert 'LONG PUT' in names
    assert 'SHORT PUT' not in names
    lp = next(r for r in recs if r['name'] == 'LONG PUT')
    assert lp['is_bullish'] is False
    assert lp['max_loss'] == 4.0
    assert lp['breakeven'] == 96.0

def test_long_put_metrics():
    s = compute_strategy('long_put', 100, 5, 105)
    assert s['max_profit'] == 95  # strike - premium: stock can only fall to 0
    assert s['max_loss'] == 5
    assert s['breakeven'] == 95
    assert s['is_bullish'] is False


def test_recommend_bearish_spread_and_covered_call():
    """Bearish now also suggests a defined-risk bear put spread and a covered
    call for income, so the user sees more than one idea."""
    chain = [
        {'strike_price': 100, 'type': 'put', 'last_price': 4.0},
        {'strike_price': 95, 'type': 'put', 'last_price': 2.0},
        {'strike_price': 105, 'type': 'call', 'last_price': 3.0},
    ]
    recs = recommend_strategies('bearish', 100, chain)
    names = [r['name'] for r in recs]
    assert 'LONG PUT' in names
    assert 'BEAR PUT SPREAD' in names
    assert 'COVERED CALL' in names
    assert 'SHORT PUT' not in names
    sp = next(r for r in recs if r['name'] == 'BEAR PUT SPREAD')
    assert sp['max_loss'] == 2.0  # debit: 4.0 - 2.0
    assert sp['breakeven'] == 98.0  # 100 - 2
    assert sp['is_bullish'] is False


def test_bear_put_spread_metrics():
    s = compute_strategy('bear_put_spread', 100, 4, 105, strike2=95, debit=2)
    assert s['name'] == 'BEAR PUT SPREAD'
    assert s['max_profit'] == 3.0  # (100-95) - 2
    assert s['max_loss'] == 2.0
    assert len(s['payoff_curve']) > 0

def test_strike_centering_picks_nearest_not_first():
    chain = [
        {'strike_price': 90, 'type': 'call', 'last_price': 10.0},
        {'strike_price': 100, 'type': 'call', 'last_price': 5.0},
        {'strike_price': 110, 'type': 'call', 'last_price': 2.0},
    ]
    recs = recommend_strategies('bullish', 108, chain)
    assert recs[0]['name'] == 'LONG CALL'
    assert '110C' in recs[0]['subtitle']  # nearest to 108, not the first (90)

def test_bull_call_spread_debit_is_mid_difference():
    chain = [
        {'strike_price': 95, 'type': 'call', 'last_price': 8.0},
        {'strike_price': 100, 'type': 'call', 'last_price': 5.0},
        {'strike_price': 106, 'type': 'call', 'last_price': 3.0},
    ]
    ind = [{'name': 'ATR(14)', 'verdict': 'hold'}]
    recs = recommend_strategies('bullish', 100, chain, indicator_results=ind)
    spread = next(r for r in recs if r['name'] == 'BULL CALL SPREAD')
    # debit = mid(95C) - mid(100C) = 3.0, NOT the first leg's full 8.0.
    assert spread['max_loss'] == 3.0
    assert spread['breakeven'] == 98.0


def test_bear_spread_never_negative_expectancy():
    """Regression: a zero-quote same-strike put in a different expiry used to
    pair with the nearest put, yielding width 0, max profit < 0, ROR -100%."""
    chain = [
        {'strike_price': 100, 'type': 'put', 'last_price': 0.85, 'expiration_date': '2026-08-28'},
        {'strike_price': 100, 'type': 'put', 'last_price': 0.0, 'expiration_date': '2026-09-11'},
        {'strike_price': 95, 'type': 'put', 'last_price': 0.40, 'expiration_date': '2026-08-28'},
        {'strike_price': 105, 'type': 'call', 'last_price': 3.0},
    ]
    recs = recommend_strategies('bearish', 100, chain)
    sp = next((r for r in recs if r['name'] == 'BEAR PUT SPREAD'), None)
    assert sp is None or sp['max_profit'] > 0
    if sp is not None:
        assert sp['return_on_risk'] > 0
        # Legs must share one expiry.
        assert sp['breakeven'] == 100 - 0.45  # 100P - (0.85 - 0.40) debit
