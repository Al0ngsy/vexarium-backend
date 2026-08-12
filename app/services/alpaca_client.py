import io
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
import pandas as pd
import httpx
from datetime import datetime, timedelta
from typing import Optional
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import (
    StockBarsRequest, StockLatestQuoteRequest, StockSnapshotRequest,
    OptionSnapshotRequest, OptionChainRequest, NewsRequest
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import OptionsFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType
from ..config import settings
from .cache import (
    cache_get, cache_set, run_coro,
    bars_key, quote_key, news_key, option_chain_key,
    CACHE_TTL_BARS, CACHE_TTL_QUOTE, CACHE_TTL_NEWS,
    CACHE_TTL_OPTION_CHAIN,
)
from .exceptions import AlpacaError, SymbolNotFoundError, SubscriptionRequiredError

logger = logging.getLogger('vexarium.alpaca')

# Yahoo fallback for symbols outside Alpaca's universe. Windows Chrome UA —
# Yahoo rate-limits/429s the macOS UA from datacenter IPs (Render); the
# Windows UA is accepted (same rationale as company_info.py).
_YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}
_EMPTY_BARS_COLUMNS = ['open', 'high', 'low', 'close', 'volume', 'timestamp']

# Finnhub — real-time intraday bars (no 15-min historical delay). Free tier:
# 60 calls/min account-wide; ONE call returns all bars for a symbol, so a
# chart refresh costs 1 call and the bar-duration cache absorbs repeats.
_FINNHUB_URL = "https://finnhub.io/api/v1/stock/candle"
_FINNHUB_RES = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60"}  # 4h has no native res → Alpaca


def _fetch_finnhub_bars(symbol: str, days: int, res: str) -> pd.DataFrame:
    """Real-time intraday OHLCV bars from Finnhub (Unix bar-start timestamps).

    Empty DataFrame on any failure (no key, non-200, no_data, bad shape) —
    callers fall through to the Alpaca/Yahoo path.
    """
    cols = _EMPTY_BARS_COLUMNS
    try:
        resp = httpx.get(
            _FINNHUB_URL,
            params={
                "symbol": symbol.upper(),
                "resolution": res,
                "from": int(datetime.now().timestamp()) - days * 86400,
                "to": int(datetime.now().timestamp()),
                "token": settings.finnhub_api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("s") != "ok" or not data.get("t"):
            return pd.DataFrame(columns=cols)
        return pd.DataFrame({
            "open": data["o"],
            "high": data["h"],
            "low": data["l"],
            "close": data["c"],
            "volume": data["v"],
            "timestamp": pd.to_datetime(data["t"], unit="s", utc=True),
        })
    except Exception:
        logger.debug("Finnhub bars failed for %s", symbol)
        return pd.DataFrame(columns=cols)


TIMEFRAMES: dict[str, tuple[int, TimeFrameUnit, int, str]] = {
    # key -> (multiplier, unit, max days, yahoo interval)
    "1m": (1, TimeFrameUnit.Minute, 5, "1m"),
    "5m": (5, TimeFrameUnit.Minute, 60, "5m"),
    "15m": (15, TimeFrameUnit.Minute, 60, "15m"),
    "30m": (30, TimeFrameUnit.Minute, 60, "30m"),
    "1h": (1, TimeFrameUnit.Hour, 730, "60m"),
    "4h": (4, TimeFrameUnit.Hour, 730, "60m"),  # ponytail: Yahoo has no 4h interval; OTC fallback serves 1h bars
    "1d": (1, TimeFrameUnit.Day, 365, "1d"),
    "1w": (1, TimeFrameUnit.Week, 1826, "1wk"),
    "1mo": (1, TimeFrameUnit.Month, 7300, "1mo"),
}


def _yahoo_range(days: int) -> str:
    """Map a requested number of days to a Yahoo chart range parameter."""
    for limit, rng in (
        (31, '1mo'), (92, '3mo'), (183, '6mo'), (365, '1y'),
        (730, '2y'), (1826, '5y'), (7300, 'max'),
    ):
        if days <= limit:
            return rng
    return 'max'


def _fetch_yahoo_bars(symbol: str, days: int = 365, interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV bars from Yahoo Finance v8 chart (keyless).

    Fallback for symbols outside Alpaca's equity universe — e.g. OTC/Pink
    Sheet ADRs like SMERY (Siemens Energy) or RNMBY (Rheinmetall), which
    trade on OTC Markets and return no bars from Alpaca. Yahoo covers them.

    Returns an empty DataFrame (never raises) on any failure. Tries query1
    then query2 hosts, same as company_info.py.
    """
    rng = _yahoo_range(days)
    last_err: Exception | None = None
    for host in ('query1', 'query2'):
        try:
            url = (
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
                f"?range={rng}&interval={interval}"
            )
            with httpx.Client(headers=_YAHOO_HEADERS, timeout=12.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json()
            result = payload['chart']['result'][0]
            timestamps = result.get('timestamp') or []
            quote = (result.get('indicators') or {}).get('quote') or [{}]
            q = quote[0] if quote else {}
            rows = []
            n = len(timestamps)
            for i, ts in enumerate(timestamps):
                close = (q.get('close') or [None] * n)[i]
                if close is None:
                    continue  # no trade that day (OTC ADRs are illiquid)
                open_ = (q.get('open') or [close] * n)[i] or close
                high = (q.get('high') or [close] * n)[i] or close
                low = (q.get('low') or [close] * n)[i] or close
                volume = (q.get('volume') or [0] * n)[i] or 0.0
                rows.append({
                    'open': float(open_),
                    'high': float(high),
                    'low': float(low),
                    'close': float(close),
                    'volume': float(volume),
                    'timestamp': pd.to_datetime(ts, unit='s', utc=True),
                })
            if rows:
                return pd.DataFrame(rows)
            last_err = RuntimeError('no rows in Yahoo response')
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    logger.warning('Yahoo bars fallback failed for %s: %s', symbol, last_err)
    return pd.DataFrame(columns=_EMPTY_BARS_COLUMNS)


class AlpacaClient:
    def __init__(self):
        creds = (settings.alpaca_api_key, settings.alpaca_secret_key)
        self._stock = StockHistoricalDataClient(*creds)
        self._option = OptionHistoricalDataClient(*creds)
        self._news = NewsClient(*creds)
        self._trading = TradingClient(*creds, paper=settings.alpaca_paper)

    def get_stock_bars(self, symbol: str, days: int = 365, timeframe: str = "1d") -> pd.DataFrame:
        """Daily (or intraday/weekly/monthly) OHLCV bars for `symbol`.

        `timeframe` is one of TIMEFRAMES keys: 1m/5m/15m/1h/1d/1w/1mo.
        """
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        mult, unit, tf_days, yahoo_interval = TIMEFRAMES[timeframe]
        if days > tf_days:
            days = tf_days  # cap at what the data source offers
        # Cache for roughly one bar duration so intraday charts stay fresh
        # without hammering Redis (1m→60s, 5m→5min, 15m→15min, …, 4h→4h);
        # daily+ bars change once per period → 6h cap.
        unit_seconds = {TimeFrameUnit.Minute: 60, TimeFrameUnit.Hour: 3600}.get(unit, 86400)
        ttl = min(mult * unit_seconds, CACHE_TTL_BARS)
        key = bars_key(symbol, timeframe)
        cached = run_coro(cache_get(key))
        if cached is not None:
            try:
                df = pd.read_json(io.StringIO(cached))
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df
            except Exception:
                pass
        # Real-time intraday bars: Finnhub first (no 15-min historical delay,
        # unlike Alpaca/Yahoo). Falls through to Alpaca/Yahoo below on any miss.
        res = _FINNHUB_RES.get(timeframe)
        if res and settings.finnhub_api_key:
            df = _fetch_finnhub_bars(symbol, days, res)
            if not df.empty:
                df.attrs["source"] = "finnhub"
                run_coro(cache_set(key, df.to_json(), ttl=ttl))
                return df
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(mult, unit),
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
                # OTC/Pink-Sheet ADRs (SMERY, RNMBY, …) return no bars from
                # Alpaca. Fall back to keyless Yahoo bars before giving up.
                df = _fetch_yahoo_bars(symbol, days, interval=yahoo_interval)
                if not df.empty:
                    df.attrs["source"] = "yahoo"  # Yahoo intraday is ~15 min delayed
                    run_coro(cache_set(key, df.to_json(), ttl=ttl))
                    return df
                return pd.DataFrame(columns=_EMPTY_BARS_COLUMNS)
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
            # Alpaca free tier (IEX) only serves ~2 years of history — 1w/1mo
            # timeframes come back with ~104/~24 rows, too few for SMA(50)/
            # EMA(200) (min_rows 201). Yahoo serves 5-10y+, so when Alpaca's
            # bars are too shallow, fetch the Yahoo series and keep the longer.
            if len(df) < 201:
                yahoo_df = _fetch_yahoo_bars(symbol, days, interval=yahoo_interval)
                if len(yahoo_df) > len(df):
                    df = yahoo_df
                    df.attrs["source"] = "yahoo"  # Yahoo intraday is ~15 min delayed
            else:
                # Alpaca bars come from IEX but the historical API excludes the
                # last ~15 min (their historical-data rule) — quotes are the
                # real-time part. The FE live-tick extends the last candle.
                df.attrs["source"] = "alpaca"
            run_coro(cache_set(key, df.to_json(), ttl=ttl))
            return df
        except Exception as e:
            logger.error("Alpaca get_stock_bars failed for %s: %s", symbol, e)
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                # Alpaca rejected the symbol outright — it may still be a real
                # OTC/ADR ticker. Try the Yahoo fallback before giving up.
                df = _fetch_yahoo_bars(symbol, days)
                if not df.empty:
                    df.attrs["source"] = "yahoo"
                    run_coro(cache_set(key, df.to_json(), ttl=ttl))
                    return df
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            raise AlpacaError(f"Failed to fetch stock bars for {symbol}")

    def get_latest_quote(self, symbol: str) -> dict:
        key = quote_key(symbol)
        cached = run_coro(cache_get(key))
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
            run_coro(cache_set(key, result, ttl=CACHE_TTL_QUOTE))
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
        """Collect distinct expiration dates across the whole range.

        Alpaca's ``get_option_contracts`` paginates from the *nearest* expiry
        and typically stops after ~1-2 pages, so far-dated (LEAPS) expiries are
        never seen. We therefore query in monthly slices across the full range,
        each slice independently, and union the expiries. This surfaces
        near-term, mid-term AND far-dated maturities.
        """
        from datetime import date, timedelta
        expiries: set[str] = set()

        def _query(slice_gte: str, slice_lte: str):
            token: Optional[str] = None
            for _ in range(10):
                kwargs: dict = {
                    'underlying_symbols': [underlying],
                    'expiration_date_gte': slice_gte,
                    'expiration_date_lte': slice_lte,
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
                if not token or not contracts:
                    break

        # Walk the range in ~31-day slices.
        try:
            d = date.fromisoformat(gte)
            end = date.fromisoformat(lte)
        except ValueError:
            return sorted([e for e in expiries if e and e != 'None'])[:max_dates]
        while d <= end:
            slice_end = min(d + timedelta(days=31), end)
            _query(d.isoformat(), slice_end.isoformat())
            d = slice_end + timedelta(days=1)

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

    def get_option_chain(
        self, underlying: str,
        expiration_gte: Optional[str] = None,
        expiration_lte: Optional[str] = None,
        strike_gte: Optional[float] = None,
        strike_lte: Optional[float] = None,
        contract_type: Optional[str] = None,
        limit: int = 2000,
        use_cache: bool = True,
    ) -> list:
        """Fetch the full option chain for an underlying via the market-data
        ``get_option_chain`` endpoint (one call, paginated).

        Returns, for **every** contract in range, the latest quote (bid/ask),
        latest trade, implied volatility and greeks — in a single endpoint
        (TradingView-style chain). Uses the free **indicative** feed by default
        (delayed quotes), which is what a paper account can access.

        Each item is a dict:
            { symbol, strike_price, expiration_date, type, bid, ask,
              last_price, implied_volatility, greeks{delta,gamma,theta,vega,rho} }

        Unlike ``get_option_contracts`` (trading metadata, where volume/OI are
        None), this gives real bid/ask + greeks per strike.
        """
        try:
            key = option_chain_key(underlying)
            if use_cache:
                cached = run_coro(cache_get(key))
                if cached is not None:
                    return cached
            results: list = []
            page_token: Optional[str] = None
            while True:
                kwargs: dict = {
                    'underlying_symbol': underlying,
                    'feed': OptionsFeed.INDICATIVE,
                    'limit': limit,
                }
                if expiration_gte:
                    kwargs['expiration_date_gte'] = expiration_gte
                if expiration_lte:
                    kwargs['expiration_date_lte'] = expiration_lte
                if strike_gte is not None:
                    kwargs['strike_price_gte'] = strike_gte
                if strike_lte is not None:
                    kwargs['strike_price_lte'] = strike_lte
                if contract_type:
                    kwargs['type'] = (
                        ContractType.CALL
                        if contract_type.lower() == 'call'
                        else ContractType.PUT
                    )
                if page_token:
                    kwargs['page_token'] = page_token
                req = OptionChainRequest(**kwargs)
                resp = self._option.get_option_chain(req)
                data = resp if isinstance(resp, dict) else getattr(resp, 'data', {})
                if not isinstance(data, dict):
                    data = {}
                for sym, snap in data.items():
                    results.append(self._snapshot_to_chain_row(sym, snap))
                # Pagination via next_page_token when using raw_data mode; the
                # wrapped SDK client returns a plain dict so this is usually None.
                token = None
                if isinstance(resp, dict) and 'next_page_token' in resp:
                    candidate = resp.get('next_page_token')
                    token = str(candidate) if candidate else None
                elif hasattr(resp, 'next_page_token'):
                    candidate = getattr(resp, 'next_page_token', None)
                    token = str(candidate) if candidate else None
                if not token:
                    break
                page_token = token
            if use_cache:
                run_coro(cache_set(key, results, ttl=CACHE_TTL_OPTION_CHAIN))
            return results
        except Exception as e:
            logger.error("Alpaca get_option_chain failed for %s: %s", underlying, e)
            err_msg = str(e).lower()
            if 'subscription' in err_msg or 'permission' in err_msg:
                raise SubscriptionRequiredError(f"Options data subscription required for {underlying}")
            raise AlpacaError(f"Failed to fetch option chain for {underlying}")

    @staticmethod
    def _parse_occ_symbol(sym: str) -> tuple:
        """Parse an OCC option symbol -> (strike, expiry_iso, is_call).

        Layout: ROOT(1-6) + YYMMDD(6) + C/P(1) + strike(8). e.g.
        SPY260814C00752000 -> (752.0, '2026-08-14', True).
        """
        s = sym.strip().upper()
        try:
            if len(s) < 16:
                return (0.0, '', True)
            cp = s[-9]
            strike = float(s[-8:]) / 1000.0
            yymmdd = s[-15:-9]
            expiry = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
            return (strike, expiry, cp == "C")
        except (ValueError, IndexError):
            return (0.0, '', True)

    @staticmethod
    def _snapshot_to_chain_row(sym: str, snap) -> dict:
        """Map an OptionsSnapshot (SDK model) to a flat chain row dict."""
        greeks = getattr(snap, 'greeks', None)
        greeks_dict = {}
        if greeks is not None:
            greeks_dict = {
                'delta': float(getattr(greeks, 'delta', 0) or 0),
                'gamma': float(getattr(greeks, 'gamma', 0) or 0),
                'theta': float(getattr(greeks, 'theta', 0) or 0),
                'vega': float(getattr(greeks, 'vega', 0) or 0),
                'rho': float(getattr(greeks, 'rho', 0) or 0),
            }
        latest_trade = getattr(snap, 'latest_trade', None)
        latest_quote = getattr(snap, 'latest_quote', None)
        strike, expiry, is_call = AlpacaClient._parse_occ_symbol(sym)
        return {
            'symbol': sym,
            'strike_price': strike,
            'expiration_date': expiry,
            'type': 'call' if is_call else 'put',
            'bid': float(getattr(latest_quote, 'bid_price', 0) or 0) if latest_quote else 0.0,
            'ask': float(getattr(latest_quote, 'ask_price', 0) or 0) if latest_quote else 0.0,
            'last_price': float(getattr(latest_trade, 'price', 0) or 0) if latest_trade else 0.0,
            'implied_volatility': float(getattr(snap, 'implied_volatility', 0) or 0),
            'greeks': greeks_dict,
        }

    # Google News RSS aggregates all major outlets (Reuters, CNBC, Seeking
    # Alpha, MarketBeat, ...) with no API key — complements Alpaca's free feed
    # (which only carries Benzinga). Windows UA avoids 429s from datacenter IPs.
    _GNEWS_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    def _fetch_google_news(self, symbol: str, limit: int = 6) -> list:
        """Keyless multi-source headlines via Google News RSS. Never raises."""
        try:
            q = urllib.parse.quote(f"{symbol} stock")
            url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
            resp = httpx.get(url, headers=self._GNEWS_HEADERS, timeout=10, follow_redirects=True)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            items = root.findall(".//item")[:limit]
            out = []
            for it in items:
                title_el = it.find("title")
                link_el = it.find("link")
                date_el = it.find("pubDate")
                src_el = it.find("source")
                desc_el = it.find("description")
                headline = (title_el.text or "").strip() if title_el is not None else ""
                if not headline:
                    continue
                # Google News links are redirect URLs; the real article lives at
                # the first url= target — keep the short form for the browser.
                link = (link_el.text or "").strip() if link_el is not None else ""
                src = (src_el.text or "").strip() if src_el is not None else ""
                published = (date_el.text or "").strip() if date_el is not None else ""
                summary = ""
                if desc_el is not None and desc_el.text:
                    # description contains a snippet + "The post ... appeared first on X"
                    summary = re.sub(r"<[^>]+>", "", desc_el.text).strip()
                    summary = re.sub(r"\s*The post .*? appeared first on .*\.?\s*$", "", summary)
                out.append({
                    "headline": headline,
                    "source": src or "Google News",
                    "url": link,
                    "summary": summary[:400],
                    "created_at": published,
                    "symbols": [symbol],
                    "id": None,
                })
            return out
        except Exception:
            logger.warning("Google News RSS failed for %s", symbol, exc_info=True)
            return []

    def get_news(self, symbol: str, limit: int = 10) -> list:
        key = news_key(symbol)
        cached = run_coro(cache_get(key))
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
            # Merge in multi-source headlines (Google News RSS) and interleave
            # them with the Alpaca/Benzinga items so the feed isn't one outlet.
            extra = self._fetch_google_news(symbol, limit=6)
            merged = []
            i, j = 0, 0
            while i < len(result) or j < len(extra):
                if j < len(extra) and (i >= len(result) or (i + j) % 3 == 2):
                    merged.append(extra[j]); j += 1
                elif i < len(result):
                    merged.append(result[i]); i += 1
                elif j < len(extra):
                    merged.append(extra[j]); j += 1
            run_coro(cache_set(key, merged, ttl=CACHE_TTL_NEWS))
            return merged
        except Exception as e:
            logger.error("Alpaca get_news failed for %s: %s", symbol, e)
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'invalid symbol' in err_msg:
                raise SymbolNotFoundError(f"Symbol not found: {symbol}")
            # Alpaca news is down: fall back to Google News RSS alone.
            extra = self._fetch_google_news(symbol, limit=8)
            if extra:
                return extra
            raise AlpacaError(f"Failed to fetch news for {symbol}")
