from datetime import datetime, date, timedelta
from math import log, sqrt, exp, erf
from typing import Optional

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1 + erf(x / sqrt(2)))


def black_scholes_price(strike: float, price: float, days_to_expiry: float,
                        implied_vol: float, risk_free: float = 0.04, is_call: bool = True) -> float:
    """Black-Scholes option price given a hypothetical underlying price."""
    if days_to_expiry <= 0:
        # At expiry: intrinsic value only.
        return max((price - strike) if is_call else (strike - price), 0)
    t = days_to_expiry / 365.0
    if t <= 0 or implied_vol <= 0 or strike <= 0:
        return max((price - strike) if is_call else (strike - price), 0)
    iv = implied_vol * sqrt(t)
    d1 = (log(price / strike) + (risk_free + 0.5 * implied_vol ** 2) * t) / iv
    d2 = d1 - iv
    if is_call:
        return price * _norm_cdf(d1) - strike * exp(-risk_free * t) * _norm_cdf(d2)
    else:
        return strike * exp(-risk_free * t) * _norm_cdf(-d2) - price * _norm_cdf(-d1)


def option_value_at_price(strike: float, premium: float, current_price: float,
                          expiry_date: str, implied_vol: float, is_call: bool,
                          target_price: float, target_date: Optional[str] = None) -> dict:
    """Estimate the option's value if the underlying trades at ``target_price``.

    Uses Black-Scholes with the contract's implied volatility. If ``target_date``
    is provided, time to expiry is computed from that date (so you can see value
    at a future date / after time decay); otherwise it defaults to expiry (0 days
    to expiry -> intrinsic value).
    """
    try:
        expiry = datetime.strptime(expiry_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        expiry = date.today()
    ref = date.today()
    if target_date:
        try:
            ref = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            ref = date.today()
    days = max((expiry - ref).days, 0)
    est = black_scholes_price(strike, target_price, days, implied_vol, is_call=is_call)
    pl = est - premium
    pl_pct = (pl / premium) if premium > 0 else 0.0
    return {
        "target_price": round(target_price, 2),
        "target_date": ref.isoformat(),
        "days_to_expiry": days,
        "estimated_option_price": round(est, 2),
        "estimated_pl": round(pl, 2),
        "pl_pct": round(pl_pct, 4),
    }


def build_payoff_matrix(strike, premium, current_price, expiry_date, implied_vol,
                        is_call, strikes=None, expiries=None, range_pct=0.05,
                        quantity=100) -> dict:
    """Build a strike × expiry P/L matrix.

    Each row is a strike (default: strikes centered ±range_pct around
    ``current_price``). Each column is an expiry date. Every cell is the
    projected P/L for buying the option at ``premium`` and holding to that
    expiry, priced via Black-Scholes. ``quantity`` defaults to 100 (per
    contract) for dollar P/L.
    """
    # Default strike ladder: centered on current price.
    if not strikes:
        lo = current_price * (1 - range_pct)
        hi = current_price * (1 + range_pct)
        step = round((hi - lo) / 20, 2) or 1.0
        strikes = [round(p, 2) for p in _frange(lo, hi, step)]
    if not expiries:
        expiries = [expiry_date]

    rows = []
    for s in strikes:
        row = {"strike": s}
        if current_price:
            row["move_pct"] = round((s - current_price) / current_price * 100, 2)
        else:
            row["move_pct"] = 0.0
        row["cells"] = []
        for exp in expiries:
            val = option_value_at_price(
                strike=strike, premium=premium, current_price=current_price,
                expiry_date=expiry_date, implied_vol=implied_vol, is_call=is_call,
                target_price=s, target_date=exp,
            )
            row["cells"].append({
                "expiry": exp,
                "days_to_expiry": val["days_to_expiry"],
                "option_value": round(val["estimated_option_price"] * quantity, 2),
                "pl": round(val["estimated_pl"] * quantity, 2),
                "pl_pct": val["pl_pct"],
            })
        rows.append(row)
    return {
        "strikes": rows,
        "expiries": expiries,
        "current_price": current_price,
        "range_pct": range_pct,
        "premium": premium,
        "breakeven": compute_breakeven(strike, premium, is_call),
    }


def _frange(lo, hi, step):
    out = []
    p = lo
    while p <= hi:
        out.append(p)
        p += step
    return out

def compute_payoff(strike: float, premium: float, current_price: float, is_call: bool) -> dict:
    if is_call:
        intrinsic = max(current_price - strike, 0)
    else:
        intrinsic = max(strike - current_price, 0)
    pl = intrinsic - premium
    pl_pct = (pl / premium) if premium > 0 else 0.0
    return {
        "strike": strike,
        "premium": premium,
        "current_price": current_price,
        "intrinsic_value": intrinsic,
        "pl": round(pl, 2),
        "pl_pct": round(pl_pct, 4),
        "is_call": is_call,
    }

def compute_breakeven(strike: float, premium: float, is_call: bool) -> float:
    if is_call:
        return strike + premium
    else:
        return strike - premium

def build_payoff_timeline(strike: float, premium: float, current_price: float,
                          expiry_date: str, theta_per_day: float, is_call: bool) -> list:
    try:
        expiry = datetime.strptime(expiry_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return []
    today = date.today()
    days_to_expiry = (expiry - today).days
    if days_to_expiry <= 0:
        return []
    timeline = []
    for d in range(days_to_expiry + 1):
        target_date = today + timedelta(days=d)
        estimated_option_price = max(premium - (theta_per_day * d), 0.0)
        if is_call:
            intrinsic = max(current_price - strike, 0)
        else:
            intrinsic = max(strike - current_price, 0)
        estimated_pl = estimated_option_price - premium
        pl_pct = (estimated_pl / premium) if premium > 0 else 0.0
        timeline.append({
            "date": target_date.isoformat(),
            "day": d,
            "estimated_option_price": round(estimated_option_price, 2),
            "estimated_pl": round(estimated_pl, 2),
            "pl_pct": round(pl_pct, 4),
        })
    return timeline
