from datetime import datetime
from app.repositories.trades import InMemoryTradeRepository

def test_create_and_list_trade():
    repo = InMemoryTradeRepository()
    trade = repo.create_trade(
        user_id=1, symbol="AAPL", trade_type="stock",
        entry_date=datetime(2026, 1, 1), entry_price=150.0
    )
    assert trade["id"] == 1
    assert trade["symbol"] == "AAPL"
    trades = repo.list_trades(user_id=1)
    assert len(trades) == 1

def test_get_trade():
    repo = InMemoryTradeRepository()
    repo.create_trade(user_id=1, symbol="AAPL", trade_type="stock",
                      entry_date=datetime(2026, 1, 1), entry_price=150.0)
    trade = repo.get_trade(user_id=1, trade_id=1)
    assert trade is not None
    assert trade["symbol"] == "AAPL"

def test_delete_trade():
    repo = InMemoryTradeRepository()
    repo.create_trade(user_id=1, symbol="AAPL", trade_type="stock",
                      entry_date=datetime(2026, 1, 1), entry_price=150.0)
    assert repo.delete_trade(user_id=1, trade_id=1) is True
    assert repo.get_trade(user_id=1, trade_id=1) is None

def test_delete_other_user_trade():
    repo = InMemoryTradeRepository()
    repo.create_trade(user_id=1, symbol="AAPL", trade_type="stock",
                      entry_date=datetime(2026, 1, 1), entry_price=150.0)
    assert repo.delete_trade(user_id=2, trade_id=1) is False

def test_list_empty():
    repo = InMemoryTradeRepository()
    assert repo.list_trades(user_id=1) == []
