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
        contract_type: Optional[str] = None, limit: int = 500,
        around_price: Optional[float] = None, max_expiries: int = 6,
    ) -> list:
        """Fetch a usable option chain (multiple expiries, both calls and puts).

        A single Alpaca call returns only ~100 contracts, filled strike-by-strike
        for the *nearest* expiry — so a naive fetch yields one expiry and one type.
        This fetches two-phase:

        1. Paginate to discover the distinct expiry dates in range.
        2. For the ``max_expiries`` nearest dates, fetch CALL + PUT contracts. If
           ``around_price`` is given, restrict strikes to a window around it so the
           picker centers on the current underlying price; otherwise fetch a broad
           window.
        """
        try:
            results: list = []
            # Single-type path: paginate that type directly.
            if contract_type:
                return self._fetch_contracts(
                    underlying, expiration_gte, expiration_lte,
                    strike_gte, strike_lte, contract_type, limit,
                )

            # Two-phase: discover expiries, then fetch per-expiry both types.
            # Spread the fetched expiries evenly across the range so the user sees
            # near-term, mid-term AND far-dated (LEAPS) strikes.
            expiries = self._discover_expiries(underlying, expiration_gte, expiration_lte)
            if not expiries:
                return []
            picked = self._spread_expiries(expiries, max_expiries)
            for exp in picked:
                for ct in ('call', 'put'):
                    results.extend(self._fetch_contracts(
                        underlying, exp, exp, strike_gte, strike_lte, ct,
                        budget=max(1, limit // (len(picked) * 2)),
                        around_price=around_price,
                    ))
            return results
        except Exception as e:
            logger.error("Alpaca get_option_contracts failed for %s: %s", underlying, e)
            err_msg = str(e).lower()
            if 'subscription' in err_msg or 'permission' in err_msg:
                raise SubscriptionRequiredError(f"Options data subscription required for {underlying}")
            raise AlpacaError(f"Failed to fetch option contracts for {underlying}")

    def _discover_expiries(self, underlying, gte, lte, max_dates=60) -> list:
        """Paginate to collect distinct expiration dates across the whole range.

        Unlike the naive "stop at 10" version, this keeps paging so we see
        far-dated expiries too (not just the nearest ones Alpaca returns first).
        ``max_dates`` here bounds the *discovery* set generously; the caller
        then samples ``max_expiries`` of them evenly across the range.
        """
        expiries = set()
        token: Optional[str] = None
        for _ in range(25):
            kwargs: dict = {
                'underlying_symbols': [underlying],
                'expiration_date_gte': gte,
                'expiration_date_lte': lte,
                'status': 'active',
                'limit': 100,
                'type': ContractType.CALL,
            }
            if token is not None:
                kwargs['page_token'] = token
            req = GetOptionContractsRequest(**kwargs)
            resp = self._trading.get_option_contracts(req)
            contracts = []
            if hasattr(resp, 'option_contracts'):
                contracts = resp.option_contracts or []
            elif isinstance(resp, dict):
                contracts = resp.get('option_contracts') or []
            for c in contracts:
                expiries.add(str(getattr(c, 'expiration_date', None)))
            if hasattr(resp, 'next_page_token'):
                token = resp.next_page_token
            elif isinstance(resp, dict):
                token = resp.get('next_page_token')
            else:
                token = None
            if not token or not contracts or len(expiries) >= max_dates:
                break
        return sorted([e for e in expiries if e and e != 'None'])[:max_dates]

    @staticmethod
    def _spread_expiries(expiries: list[str], n: int) -> list[str]:
        """Pick ``n`` expiries spread evenly across the sorted range.

        Choosing evenly (not just the nearest ``n``) means the user sees
        near-term, mid-term AND far-dated (LEAPS) strikes — not just the next
        few weeks.
        """
        if not expiries or n <= 0:
            return expiries
        if len(expiries) <= n:
            return expiries
        step = (len(expiries) - 1) / (n - 1) if n > 1 else 0
        idxs = [round(i * step) for i in range(n)]
        # De-dupe (rounding can collide) and backfill if needed.
        seen: list[str] = []
        for i in idxs:
            e = expiries[i]
            if e not in seen:
                seen.append(e)
        # Backfill any remaining slots with the earliest not-yet-seen expiries.
        if len(seen) < n:
            for e in expiries:
                if e not in seen:
                    seen.append(e)
                if len(seen) >= n:
                    break
        return seen[:n]


    def _fetch_contracts(self, underlying, gte, lte, strike_gte, strike_lte,
                         contract_type, budget, around_price=None) -> list:
        """Fetch one contract type in a date window, paginating until budget or done."""
        results: list = []
        token: Optional[str] = None
        fetched = 0
        while fetched < budget:
            kwargs: dict = {
                'underlying_symbols': [underlying],
                'expiration_date_gte': gte,
                'expiration_date_lte': lte,
                'status': 'active',
                'limit': 100,
                'type': ContractType.CALL if contract_type.lower() == 'call' else ContractType.PUT,
            }
            if strike_gte is not None:
                kwargs['strike_price_gte'] = str(strike_gte)
            if strike_lte is not None:
                kwargs['strike_price_lte'] = str(strike_lte)
            if around_price is not None:
                # Window strikes within ±12% of the reference price so the picker
                # centers on the current underlying price.
                lo = round(around_price * 0.88, 2)
                hi = round(around_price * 1.12, 2)
                kwargs['strike_price_gte'] = str(lo)
                kwargs['strike_price_lte'] = str(hi)
            if token is not None:
                kwargs['page_token'] = token
            req = GetOptionContractsRequest(**kwargs)
            resp = self._trading.get_option_contracts(req)
            contracts = []
            if hasattr(resp, 'option_contracts'):
                contracts = resp.option_contracts or []
            elif isinstance(resp, dict):
                contracts = resp.get('option_contracts') or []
            for c in contracts:
                results.append(c.model_dump() if hasattr(c, 'model_dump') else dict(c))
            if hasattr(resp, 'next_page_token'):
                token = resp.next_page_token
            elif isinstance(resp, dict):
                token = resp.get('next_page_token')
            else:
                token = None
            if not token or not contracts:
                break
            fetched += len(contracts)
        return results

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
