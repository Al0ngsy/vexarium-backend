from datetime import datetime, date, timedelta
from typing import Optional

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
