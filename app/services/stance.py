from datetime import datetime, date
from ..config import settings

def compute_stance(entry_price: float, current_price: float, trade_type: str = "stock", contract: dict = None) -> dict:
    pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0
    take_profit_at = entry_price * (1 + settings.take_profit_threshold)
    cut_loss_at = entry_price * (1 + settings.cut_loss_threshold)
    
    if pnl_pct >= settings.take_profit_threshold:
        stance = "TAKE_PROFIT"
        reason = f"P/L of {pnl_pct:.1%} exceeds take-profit threshold of {settings.take_profit_threshold:.0%}. Consider selling to lock in gains."
    elif pnl_pct <= settings.cut_loss_threshold:
        stance = "CUT_LOSS"
        reason = f"P/L of {pnl_pct:.1%} below cut-loss threshold of {settings.cut_loss_threshold:.0%}. Consider exiting to limit further losses."
    elif contract and trade_type == "option":
        expiry_str = contract.get("expiration_date") or contract.get("expiry_date")
        if expiry_str:
            try:
                expiry = datetime.strptime(expiry_str[:10], "%Y-%m-%d").date()
                days_to_expiry = (expiry - date.today()).days
                if days_to_expiry < 7 and pnl_pct > 0 and pnl_pct < 0.05:
                    stance = "TAKE_PROFIT"
                    reason = f"Option expires in {days_to_expiry} days with small profit of {pnl_pct:.1%}. Theta decay will erode remaining value — take profit now."
                else:
                    stance = "HOLD"
                    reason = f"P/L of {pnl_pct:.1%} within normal range. {days_to_expiry} days to expiry."
            except (ValueError, TypeError):
                stance = "HOLD"
                reason = f"P/L of {pnl_pct:.1%} within normal range."
        else:
            stance = "HOLD"
            reason = f"P/L of {pnl_pct:.1%} within normal range."
    else:
        stance = "HOLD"
        reason = f"P/L of {pnl_pct:.1%} within normal range."
    
    return {
        "stance": stance,
        "reason": reason,
        "pnl_pct": round(pnl_pct, 4),
        "take_profit_at": round(take_profit_at, 2),
        "cut_loss_at": round(cut_loss_at, 2),
    }
