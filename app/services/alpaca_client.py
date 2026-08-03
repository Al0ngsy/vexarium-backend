import pandas as pd
from datetime import datetime, timedelta, date
from typing import Optional
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import (
    StockBarsRequest, StockLatestQuoteRequest, OptionSnapshotRequest, NewsRequest
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType
from ..config import settings
from .exceptions import AlpacaError, SymbolNotFoundError, SubscriptionRequiredError


class AlpacaClient:
    def __init__(self):
        creds = (settings.alpaca_api_key, settings.alpaca_secret_key)
        self._stock = StockHistoricalDataClient(*creds)
        self._option = OptionHistoricalDataClient(*creds)
        self._news = NewsClient(*creds)
        self._trading = TradingClient(*creds, paper=settings.alpaca_paper)

    def get_stock_bars(self, symbol: str, days: int = 365) -> pd.DataFrame:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=datetime.now() - timedelta(days=days),
            )
            resp = self._stock.get_stock_bars(req)
            # resp is a dict-like {symbol: [Bar, Bar, ...]}
            bars = resp.data.get(symbol, []) if hasattr(resp, 'data') else []
            if hasattr(resp, 'data') and isinstance(resp.data, dict):
                bars = resp.data.get(symbol, [])
            elif hasattr(resp, 'bars'):
                bars = resp.bars.get(symbol, []) if isinstance(resp.bars, dict) else (resp.bars or [])
            else:
                bars = []
            if not bars:
                return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume', 'timestamp'])
            rows = []
            for bar in bars:
                rows.append({
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': float(bar.volume) if bar.volume else 0.0,
                    'timestamp': bar.timestamp,
                })
            return pd.DataFrame(rows)
        except Exception as e:
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            raise AlpacaError(f"Failed to fetch stock bars for {symbol}: {e}")

    def get_latest_quote(self, symbol: str) -> dict:
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            resp = self._stock.get_stock_latest_quote(req)
            quote = resp.get(symbol) if isinstance(resp, dict) else resp
            if quote is None:
                return {}
            return {
                'bid': float(getattr(quote, 'bid_price', 0) or 0),
                'ask': float(getattr(quote, 'ask_price', 0) or 0),
                'last_price': float(getattr(quote, 'bid_price', 0) or getattr(quote, 'ask_price', 0) or 0),
                'timestamp': getattr(quote, 'timestamp', None),
            }
        except Exception as e:
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            raise AlpacaError(f"Failed to fetch latest quote for {symbol}: {e}")

    def get_option_contracts(
        self, underlying: str, expiration_gte: str, expiration_lte: str,
        strike_gte: Optional[float] = None, strike_lte: Optional[float] = None,
        contract_type: Optional[str] = None
    ) -> list:
        try:
            kwargs = {
                'underlying_symbols': [underlying],
                'expiration_date_gte': expiration_gte,
                'expiration_date_lte': expiration_lte,
                'status': 'active',
            }
            if strike_gte is not None:
                kwargs['strike_price_gte'] = str(strike_gte)
            if strike_lte is not None:
                kwargs['strike_price_lte'] = str(strike_lte)
            if contract_type is not None:
                ct = contract_type.lower()
                if ct in ('call', 'put'):
                    kwargs['type'] = ContractType.CALL if ct == 'call' else ContractType.PUT
            req = GetOptionContractsRequest(**kwargs)
            resp = self._trading.get_option_contracts(req)
            contracts = resp.option_contracts if hasattr(resp, 'option_contracts') else []
            result = []
            for c in contracts:
                d = c.model_dump() if hasattr(c, 'model_dump') else dict(c)
                result.append(d)
            return result
        except Exception as e:
            err_msg = str(e).lower()
            if 'subscription' in err_msg or 'permission' in err_msg:
                raise SubscriptionRequiredError(f"Options data subscription required: {e}")
            raise AlpacaError(f"Failed to fetch option contracts for {underlying}: {e}")

    def get_option_snapshot(self, symbol: str) -> dict:
        try:
            req = OptionSnapshotRequest(symbol_or_symbols=symbol)
            resp = self._option.get_option_snapshot(req)
            snap = resp.get(symbol) if isinstance(resp, dict) else resp
            if snap is None:
                return {}
            greeks = getattr(snap, 'greeks', None)
            greeks_dict = {}
            if greeks:
                greeks_dict = {
                    'delta': float(getattr(greeks, 'delta', 0) or 0),
                    'gamma': float(getattr(greeks, 'gamma', 0) or 0),
                    'theta': float(getattr(greeks, 'theta', 0) or 0),
                    'vega': float(getattr(greeks, 'vega', 0) or 0),
                    'rho': float(getattr(greeks, 'rho', 0) or 0),
                }
            latest_trade = getattr(snap, 'latest_trade', None)
            latest_quote = getattr(snap, 'latest_quote', None)
            return {
                'greeks': greeks_dict,
                'implied_volatility': float(getattr(snap, 'implied_volatility', 0) or 0),
                'latest_trade_price': float(getattr(latest_trade, 'price', 0) or 0) if latest_trade else 0.0,
                'latest_trade_timestamp': getattr(latest_trade, 'timestamp', None) if latest_trade else None,
                'bid': float(getattr(latest_quote, 'bid_price', 0) or 0) if latest_quote else 0.0,
                'ask': float(getattr(latest_quote, 'ask_price', 0) or 0) if latest_quote else 0.0,
            }
        except Exception as e:
            err_msg = str(e).lower()
            if 'subscription' in err_msg or 'permission' in err_msg:
                raise SubscriptionRequiredError(f"Options data subscription required: {e}")
            raise AlpacaError(f"Failed to fetch option snapshot for {symbol}: {e}")

    def get_news(self, symbol: str, limit: int = 10) -> list:
        try:
            req = NewsRequest(symbols=symbol, limit=limit)
            resp = self._news.get_news(req)
            # NewsSet.data is {"news": [News, ...]}
            articles = []
            if hasattr(resp, "data") and isinstance(resp.data, dict):
                for lst in resp.data.values():
                    if isinstance(lst, list):
                        articles.extend(lst)
            elif isinstance(resp, list):
                articles = resp
            result = []
            for article in articles:
                d = article.model_dump() if hasattr(article, "model_dump") else dict(article)
                result.append(d)
            return result
        except Exception as e:
            raise AlpacaError(f"Failed to fetch news for {symbol}: {e}")

    def get_market_calendar(self, start: str = None, end: str = None) -> list:
        try:
            from alpaca.trading.requests import GetCalendarRequest
            if start is None:
                start = date.today().isoformat()
            if end is None:
                end = (date.today() + timedelta(days=30)).isoformat()
            req = GetCalendarRequest(start=start, end=end)
            resp = self._trading.get_calendar(req)
            result = []
            for day in resp:
                d = day.model_dump() if hasattr(day, 'model_dump') else dict(day)
                result.append(d)
            return result
        except Exception as e:
            raise AlpacaError(f"Failed to fetch market calendar: {e}")
