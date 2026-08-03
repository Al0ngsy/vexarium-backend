from typing import Protocol, Optional
from datetime import datetime

class TradeRepository(Protocol):
    def create_trade(self, user_id: int, symbol: str, trade_type: str,
                     entry_date: datetime, entry_price: float,
                     quantity: float = 1.0, contract: Optional[str] = None,
                     notes: Optional[str] = None) -> dict: ...
    def list_trades(self, user_id: int) -> list[dict]: ...
    def get_trade(self, user_id: int, trade_id: int) -> Optional[dict]: ...
    def delete_trade(self, user_id: int, trade_id: int) -> bool: ...

class InMemoryTradeRepository:
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
        return trade
    def list_trades(self, user_id):
        return [t for t in self._trades.values() if t["user_id"] == user_id]
    def get_trade(self, user_id, trade_id):
        t = self._trades.get(trade_id)
        return t if t and t["user_id"] == user_id else None
    def delete_trade(self, user_id, trade_id):
        t = self._trades.get(trade_id)
        if t and t["user_id"] == user_id:
            del self._trades[trade_id]
            return True
        return False

class PostgresTradeRepository:
    def __init__(self, session_factory):
        self._session_factory = session_factory
    def create_trade(self, user_id, symbol, trade_type, entry_date, entry_price,
                     quantity=1.0, contract=None, notes=None):
        raise NotImplementedError("PostgresTradeRepository will be implemented in Phase 8")
    def list_trades(self, user_id):
        raise NotImplementedError
    def get_trade(self, user_id, trade_id):
        raise NotImplementedError
    def delete_trade(self, user_id, trade_id):
        raise NotImplementedError
