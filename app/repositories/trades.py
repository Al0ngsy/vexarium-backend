"""In-memory trade persistence.

Trades are kept in memory only (module-level singleton in the API layer).
Postgres persistence was never built — the model is in ``app.models.trade``
if/when it is.
"""
from datetime import datetime
from typing import Optional

from ..logging import get_logger

logger = get_logger("trades")

class InMemoryTradeRepository:
    # ponytail: in-memory only — trades vanish on restart. Ship Postgres-backed
    # trades (models/trade.py already exists) before this feature goes live.
    def __init__(self):
        self._trades: dict[int, dict] = {}
        self._next_id = 1
    def create_trade(self, user_id, symbol, trade_type, entry_date, entry_price,
                     quantity=1.0, contract=None, notes=None):
        trade = {
            "id": self._next_id, "user_id": user_id, "symbol": symbol,
            "trade_type": trade_type, "entry_date": entry_date.isoformat(),
            "entry_price": entry_price, "quantity": quantity,
            "contract": contract, "notes": notes,
        }
        self._trades[self._next_id] = trade
        self._next_id += 1
        logger.info("trade created id=%s user_id=%s symbol=%s type=%s", trade["id"], user_id, symbol, trade_type)
        return trade
    def list_trades(self, user_id):
        trades = [t for t in self._trades.values() if t["user_id"] == user_id]
        logger.debug("trades listed user_id=%s count=%d", user_id, len(trades))
        return trades
    def get_trade(self, user_id, trade_id):
        t = self._trades.get(trade_id)
        found = bool(t and t["user_id"] == user_id)
        logger.debug("trade fetched user_id=%s id=%s found=%s", user_id, trade_id, found)
        return t if found else None
    def delete_trade(self, user_id, trade_id):
        t = self._trades.get(trade_id)
        if t and t["user_id"] == user_id:
            del self._trades[trade_id]
            logger.info("trade deleted user_id=%s id=%s", user_id, trade_id)
            return True
        return False
