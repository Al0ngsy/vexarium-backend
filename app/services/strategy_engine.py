from typing import Optional


def build_payoff_curve(strike, premium, current_price, is_call, price_range=None):
    if price_range is None:
        price_range = [round(current_price * 0.9, 2), round(current_price * 1.1, 2)]
    lo, hi = price_range
    step = round((hi - lo) / 40, 2) or 0.5
    curve = []
    p = lo
    while p <= hi:
        if is_call:
            intrinsic = max(p - strike, 0)
        else:
            intrinsic = max(strike - p, 0)
        pl = round(intrinsic - premium, 2)
        curve.append({"price": round(p, 2), "pl": pl})
        p += step
    return curve


def _long_call(strike, premium, current_price):
    return {
        "name": "LONG CALL",
        "subtitle": f"Buy {int(strike)}C — profit if price rises above ${strike + premium:.2f}",
        "is_bullish": True,
        "max_profit": None,
        "max_loss": round(premium, 2),
        "breakeven": round(strike + premium, 2),
        "return_on_risk": None,
        "payoff_curve": build_payoff_curve(strike, premium, current_price, True),
    }


def _cash_secured_put(strike, premium, current_price):
    max_loss = round(strike - premium, 2)
    ror = round(premium / max_loss, 4) if max_loss else 0
    return {
        "name": "CASH-SECURED PUT",
        "subtitle": f"Sell {int(strike)}P — have cash to buy shares if assigned",
        "is_bullish": True,
        "max_profit": round(premium, 2),
        "max_loss": max_loss,
        "breakeven": round(strike - premium, 2),
        "return_on_risk": ror,
        "payoff_curve": build_payoff_curve(strike, premium, current_price, False),
    }


def _covered_call(strike, premium, current_price):
    max_profit = round((strike - current_price) + premium, 2)
    return {
        "name": "COVERED CALL",
        "subtitle": f"Own shares, sell {int(strike)}C — collect premium",
        "is_bullish": False,
        "max_profit": max_profit,
        "max_loss": None,
        "breakeven": round(current_price - premium, 2),
        "return_on_risk": None,
        "payoff_curve": build_payoff_curve(strike, premium, current_price, False),
    }


def _bull_call_spread(strike1, strike2, debit, current_price):
    max_profit = round((strike2 - strike1) - debit, 2)
    # Payoff curve: buy strike1 call, sell strike2 call (both long expiry).
    lo, hi = current_price * 0.9, current_price * 1.1
    step = round((hi - lo) / 40, 2) or 0.5
    curve = []
    p = lo
    while p <= hi:
        long_leg = max(p - strike1, 0)
        short_leg = max(p - strike2, 0)
        pl = round((long_leg - short_leg) - debit, 2)
        curve.append({"price": round(p, 2), "pl": pl})
        p += step
    return {
        "name": "BULL CALL SPREAD",
        "subtitle": f"Buy {int(strike1)}C, sell {int(strike2)}C — capped upside",
        "is_bullish": True,
        "max_profit": max_profit,
        "max_loss": round(debit, 2),
        "breakeven": round(strike1 + debit, 2),
        "return_on_risk": round(max_profit / debit, 4) if debit else 0,
        "payoff_curve": curve,
    }


def _short_put(strike, premium, current_price):
    return {
        "name": "SHORT PUT",
        "subtitle": f"Sell {int(strike)}P — collect premium, profit if price stays above breakeven",
        "is_bullish": True,
        "max_profit": round(premium, 2),
        "max_loss": round(strike - premium, 2),
        "breakeven": round(strike - premium, 2),
        "return_on_risk": round(premium / (strike - premium), 4) if strike - premium else 0,
        "payoff_curve": build_payoff_curve(strike, premium, current_price, False),
    }


def compute_strategy(strategy_type, strike, premium, current_price, strike2=None, debit=None):
    strategies = {
        "long_call": _long_call,
        "cash_secured_put": _cash_secured_put,
        "covered_call": _covered_call,
        "short_put": _short_put,
    }
    if strategy_type == "bull_call_spread":
        # Requires a second strike; debit falls back to the first leg's premium.
        if not strike2:
            raise ValueError("bull_call_spread requires strike2")
        return _bull_call_spread(strike, strike2, debit if debit else premium, current_price)
    if strategy_type in strategies:
        return strategies[strategy_type](strike, premium, current_price)
    raise ValueError(f"Unknown strategy: {strategy_type}")


def _direction_from_indicators(indicator_results) -> str:
    """Derive a directional bias from technical indicators (not just the overall
    verdict string). Returns 'bullish', 'bearish', or 'neutral'."""
    if not indicator_results:
        return 'neutral'
    bullish = bearish = 0
    for r in indicator_results:
        v = str(r.get('verdict', '')).lower()
        if v in ('strong_buy', 'buy'):
            bullish += 1
        elif v in ('strong_sell', 'sell'):
            bearish += 1
    net = bullish - bearish
    if net >= 2:
        return 'bullish'
    if net <= -2:
        return 'bearish'
    return 'neutral'


def _volatility_from_indicators(indicator_results) -> str:
    """High/low volatility heuristic from indicators (ATR, Bollinger)."""
    if not indicator_results:
        return 'medium'
    for r in indicator_results:
        name = str(r.get('name', '')).upper()
        if 'ATR' in name:
            return 'high'  # ATR present and computed = use defined-risk framing
    return 'medium'


def recommend_strategies(sentiment, current_price, option_chain, indicator_results=None):
    if not option_chain:
        return []
    calls = [c for c in option_chain if str(c.get('type', '')).lower() == 'call']
    puts = [c for c in option_chain if str(c.get('type', '')).lower() == 'put']
    result = []
    # Prefer indicator-derived direction, fall back to the passed sentiment string.
    s = _direction_from_indicators(indicator_results)
    if s == 'neutral':
        s = (sentiment or 'neutral').lower()
    s = s if s in ('bullish', 'bearish', 'neutral') else 'neutral'
    # Volatility influences whether we lead with defined-risk spreads.
    vol = _volatility_from_indicators(indicator_results)
    if s == 'bullish' and calls:
        c = calls[0]
        if vol == 'high' and len(calls) > 1:
            # High vol -> suggest a defined-risk bull call spread.
            result.append(compute_strategy('bull_call_spread', c['strike_price'], c.get('last_price', 0) or 0, current_price,
                                           strike2=calls[1]['strike_price'], debit=(c.get('last_price', 0) or 0)))
        else:
            result.append(compute_strategy('long_call', c['strike_price'], c.get('last_price', 0) or 0, current_price))
            result.append(compute_strategy('cash_secured_put', c['strike_price'], c.get('last_price', 0) or 0, current_price))
    elif s == 'bearish' and puts:
        p = puts[0]
        result.append(compute_strategy('short_put', p['strike_price'], p.get('last_price', 0) or 0, current_price))
    else:
        if calls:
            c = calls[0]
            result.append(compute_strategy('long_call', c['strike_price'], c.get('last_price', 0) or 0, current_price))
        if puts:
            p = puts[0]
            result.append(compute_strategy('short_put', p['strike_price'], p.get('last_price', 0) or 0, current_price))
    return result
