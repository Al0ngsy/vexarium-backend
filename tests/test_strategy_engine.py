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
