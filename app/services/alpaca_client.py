import asyncio
import io
import logging
import threading
import pandas as pd
from datetime import datetime, timedelta, date
from typing import Optional
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import (
    StockBarsRequest, StockLatestQuoteRequest, StockSnapshotRequest,
    OptionSnapshotRequest, NewsRequest
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType
from ..config import settings
from .cache import (
    cache_get, cache_set,
    bars_key, quote_key, news_key,
    CACHE_TTL_BARS, CACHE_TTL_QUOTE, CACHE_TTL_NEWS,
)
from .exceptions import AlpacaError, SymbolNotFoundError, SubscriptionRequiredError

logger = logging.getLogger('vexarium.alpaca')


def _run_coro(coro):
    """Run an async cache call whether or not an event loop is active."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Running inside an event loop already: execute in a worker thread.
    result = {}
    def _worker():
        result["value"] = asyncio.run(coro)
    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    return result["value"]


class AlpacaClient:
    def __init__(self):
        creds = (settings.alpaca_api_key, settings.alpaca_secret_key)
        self._stock = StockHistoricalDataClient(*creds)
        self._option = OptionHistoricalDataClient(*creds)
        self._news = NewsClient(*creds)
        self._trading = TradingClient(*creds, paper=settings.alpaca_paper)

    def get_stock_bars(self, symbol: str, days: int = 365) -> pd.DataFrame:
        key = bars_key(symbol)
        cached = _run_coro(cache_get(key))
        if cached is not None:
            try:
                df = pd.read_json(io.StringIO(cached))
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df
            except Exception:
                pass
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
            df = pd.DataFrame(rows)
            _run_coro(cache_set(key, df.to_json(), ttl=CACHE_TTL_BARS))
            return df
        except Exception as e:
            logger.error("Alpaca get_stock_bars failed for %s: %s", symbol, e)
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            raise AlpacaError(f"Failed to fetch stock bars for {symbol}")

    def get_latest_quote(self, symbol: str) -> dict:
        key = quote_key(symbol)
        cached = _run_coro(cache_get(key))
        if cached is not None:
            return cached
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            resp = self._stock.get_stock_latest_quote(req)
            quote = resp.get(symbol) if isinstance(resp, dict) else resp
            if quote is None:
                return {}
            result = {
                'bid': float(getattr(quote, 'bid_price', 0) or 0),
                'ask': float(getattr(quote, 'ask_price', 0) or 0),
                'last_price': float(getattr(quote, 'bid_price', 0) or getattr(quote, 'ask_price', 0) or 0),
                'timestamp': getattr(quote, 'timestamp', None),
            }
            _run_coro(cache_set(key, result, ttl=CACHE_TTL_QUOTE))
            return result
        except Exception as e:
            logger.error("Alpaca get_latest_quote failed for %s: %s", symbol, e)
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            raise AlpacaError(f"Failed to fetch latest quote for {symbol}")

    def get_market_snapshot(self, symbol: str, df: Optional[pd.DataFrame] = None) -> dict:
        """Current market snapshot + price statistics for a symbol.

        Combines the live quote/trade (Alpaca snapshot endpoint) with statistics
        derived from the daily bars (52-week high/low, YTD change). Used to give
        the AI richer company/market context. Never raises on partial data.
        """
        result: dict[str, Optional[float]] = {
            'price': None, 'day_change_pct': None, 'bid': None, 'ask': None,
            'prev_close': None, 'high_52w': None, 'low_52w': None, 'ytd_change_pct': None,
        }
        # 1. Live quote/trade from snapshot endpoint.
        try:
            req = StockSnapshotRequest(symbol_or_symbols=symbol)
            snap = self._stock.get_stock_snapshot(req)
            snap = snap.get(symbol) if isinstance(snap, dict) else snap
            daily = getattr(snap, 'daily_bar', None)
            prev = getattr(snap, 'previous_daily_bar', None)
            trade = getattr(snap, 'latest_trade', None)
            quote = getattr(snap, 'latest_quote', None)
            if trade is not None:
                result['price'] = float(getattr(trade, 'price', 0) or 0)
            if quote is not None:
                result['bid'] = float(getattr(quote, 'bid_price', 0) or 0)
                result['ask'] = float(getattr(quote, 'ask_price', 0) or 0)
            if daily is not None:
                result['price'] = float(getattr(daily, 'close', 0) or result['price'] or 0)
                if prev is not None:
                    prev_close = float(getattr(prev, 'close', 0) or 0)
                    result['prev_close'] = prev_close
                    px = result['price']
                    if px and prev_close:
                        result['day_change_pct'] = round((px - prev_close) / prev_close * 100, 2)
        except Exception as e:
            logger.error("Alpaca get_market_snapshot (live) failed for %s: %s", symbol, e)

        # 2. Statistics from daily bars (52-week high/low, YTD).
        if df is not None and not df.empty and 'close' in df:
            closes = df['close'].dropna()
            if len(closes) > 0:
                result['high_52w'] = round(float(closes.max()), 2)
                result['low_52w'] = round(float(closes.min()), 2)
                # YTD: compare first vs last close of the current year.
                try:
                    df2 = df.copy()
                    if 'timestamp' in df2.columns:
                        df2['year'] = pd.to_datetime(df2['timestamp']).dt.year
                        cur_year = datetime.now().year
                        ytd = df2[df2['year'] == cur_year]
                        if len(ytd) >= 2:
                            first = float(ytd['close'].iloc[0])
                            last = float(ytd['close'].iloc[-1])
                            if first:
                                result['ytd_change_pct'] = round((last - first) / first * 100, 2)
                except Exception:
                    pass
        return result

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
            logger.error("Alpaca get_option_contracts failed for %s: %s", underlying, e)
            err_msg = str(e).lower()
            if 'subscription' in err_msg or 'permission' in err_msg:
                raise SubscriptionRequiredError(f"Options data subscription required for {underlying}")
            raise AlpacaError(f"Failed to fetch option contracts for {underlying}")

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
            logger.error("Alpaca get_option_snapshot failed for %s: %s", symbol, e)
            err_msg = str(e).lower()
            if 'subscription' in err_msg or 'permission' in err_msg:
                raise SubscriptionRequiredError(f"Options data subscription required for {symbol}")
            raise AlpacaError(f"Failed to fetch option snapshot for {symbol}")

    def get_news(self, symbol: str, limit: int = 10) -> list:
        key = news_key(symbol)
        cached = _run_coro(cache_get(key))
        if cached is not None:
            return cached
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
            _run_coro(cache_set(key, result, ttl=CACHE_TTL_NEWS))
            return result
        except Exception as e:
            logger.error("Alpaca get_news failed for %s: %s", symbol, e)
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            raise AlpacaError(f"Failed to fetch news for {symbol}")

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
            logger.error("Alpaca get_market_calendar failed: %s", e)
            raise AlpacaError("Failed to fetch market calendar")
