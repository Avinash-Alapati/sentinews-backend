import asyncio
import csv
import datetime
import gzip
import io
import logging
import urllib.parse
from typing import Dict, List, Optional
import httpx
import pytz

from app.core.config import settings
from app.infrastructure.observability.http_tracer import create_traced_async_client
from app.integrations.market.base import BaseMarketProvider
from app.integrations.market.indian_market_provider import IndianMarketProvider
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    MarketMover,
    CandleData,
    StockHistoryResponse,
    StockSearchResult,
)

logger = logging.getLogger(__name__)


class UpstoxMarketProvider(BaseMarketProvider):
    """
    Upstox v2 Market Data Provider with high-speed intraday feed,
    pre-mapped instruments, token-based authentication support,
    and automatic fallback to IndianMarketProvider.
    """

    BASE_URL = "https://api.upstox.com/v2"

    INDEX_INSTRUMENT_MAP = {
        "^NSEI": ("NSE_INDEX|Nifty 50", "NIFTY 50"),
        "NIFTY 50": ("NSE_INDEX|Nifty 50", "NIFTY 50"),
        "^BSESN": ("BSE_INDEX|SENSEX", "SENSEX"),
        "SENSEX": ("BSE_INDEX|SENSEX", "SENSEX"),
        "^NSEBANK": ("NSE_INDEX|Nifty Bank", "NIFTY BANK"),
        "NIFTY BANK": ("NSE_INDEX|Nifty Bank", "NIFTY BANK"),
        "^CNXIT": ("NSE_INDEX|Nifty IT", "NIFTY IT"),
        "NIFTY IT": ("NSE_INDEX|Nifty IT", "NIFTY IT"),
        "^CNXAUTO": ("NSE_INDEX|Nifty Auto", "NIFTY AUTO"),
        "NIFTY AUTO": ("NSE_INDEX|Nifty Auto", "NIFTY AUTO"),
        "^CNXPHARMA": ("NSE_INDEX|Nifty Pharma", "NIFTY PHARMA"),
        "NIFTY PHARMA": ("NSE_INDEX|Nifty Pharma", "NIFTY PHARMA"),
        "^CNXFMCG": ("NSE_INDEX|Nifty FMCG", "NIFTY FMCG"),
        "NIFTY FMCG": ("NSE_INDEX|Nifty FMCG", "NIFTY FMCG"),
        "^CNXREALTY": ("NSE_INDEX|Nifty Realty", "NIFTY REALTY"),
        "NIFTY REALTY": ("NSE_INDEX|Nifty Realty", "NIFTY REALTY"),
        "^CNXMETAL": ("NSE_INDEX|Nifty Metal", "NIFTY METAL"),
        "NIFTY METAL": ("NSE_INDEX|Nifty Metal", "NIFTY METAL"),
        "^CNXENERGY": ("NSE_INDEX|Nifty Energy", "NIFTY ENERGY"),
        "NIFTY ENERGY": ("NSE_INDEX|Nifty Energy", "NIFTY ENERGY"),
        "^CNXMEDIA": ("NSE_INDEX|Nifty Media", "NIFTY MEDIA"),
        "NIFTY MEDIA": ("NSE_INDEX|Nifty Media", "NIFTY MEDIA"),
        "^CNXPSUBANK": ("NSE_INDEX|Nifty PSU Bank", "NIFTY PSU BANK"),
        "NIFTY PSU BANK": ("NSE_INDEX|Nifty PSU Bank", "NIFTY PSU BANK"),
        "NIFTY_MIDCAP_100.NS": ("NSE_INDEX|NIFTY MIDCAP 100", "NIFTY MIDCAP 100"),
        "NIFTY MIDCAP 100": ("NSE_INDEX|NIFTY MIDCAP 100", "NIFTY MIDCAP 100"),
    }

    EQUITY_INSTRUMENT_MAP = {
        "RELIANCE": ("NSE_EQ|INE002A01018", "Reliance Industries Ltd"),
        "TCS": ("NSE_EQ|INE467B01029", "Tata Consultancy Services Ltd"),
        "HDFCBANK": ("NSE_EQ|INE040A01034", "HDFC Bank Ltd"),
        "INFY": ("NSE_EQ|INE009A01021", "Infosys Ltd"),
        "ICICIBANK": ("NSE_EQ|INE090A01021", "ICICI Bank Ltd"),
        "BHARTIARTL": ("NSE_EQ|INE397D01024", "Bharti Airtel Ltd"),
        "SBIN": ("NSE_EQ|INE062A01020", "State Bank of India"),
        "ITC": ("NSE_EQ|INE154A01025", "ITC Ltd"),
        "HINDUNILVR": ("NSE_EQ|INE030A01027", "Hindustan Unilever Ltd"),
        "LT": ("NSE_EQ|INE018A01030", "Larsen & Toubro Ltd"),
        "BAJFINANCE": ("NSE_EQ|INE296A01032", "Bajaj Finance Ltd"),
        "SUNPHARMA": ("NSE_EQ|INE044A01036", "Sun Pharmaceutical Ind Ltd"),
        "MARUTI": ("NSE_EQ|INE585B01010", "Maruti Suzuki India Ltd"),
        "AXISBANK": ("NSE_EQ|INE238A01034", "Axis Bank Ltd"),
        "TITAN": ("NSE_EQ|INE280A01028", "Titan Company Ltd"),
        "WIPRO": ("NSE_EQ|INE075A01022", "Wipro Ltd"),
        "ADANIENT": ("NSE_EQ|INE423A01024", "Adani Enterprises Ltd"),
        "POWERGRID": ("NSE_EQ|INE752E01010", "Power Grid Corp of India"),
        "M&M": ("NSE_EQ|INE101A01026", "Mahindra & Mahindra Ltd"),
        "NTPC": ("NSE_EQ|INE733E01010", "NTPC Ltd"),
        "ONGC": ("NSE_EQ|INE213A01029", "Oil & Natural Gas Corp Ltd"),
        "COALINDIA": ("NSE_EQ|INE522F01014", "Coal India Ltd"),
        "TATASTEEL": ("NSE_EQ|INE081A01020", "Tata Steel Ltd"),
        "JSWSTEEL": ("NSE_EQ|INE019A01038", "JSW Steel Ltd"),
        "HINDALCO": ("NSE_EQ|INE038A01020", "Hindalco Industries Ltd"),
        "HCLTECH": ("NSE_EQ|INE860A01027", "HCL Technologies Ltd"),
        "TECHM": ("NSE_EQ|INE669C01036", "Tech Mahindra Ltd"),
        "ASIANPAINT": ("NSE_EQ|INE021A01026", "Asian Paints Ltd"),
        "NESTLEIND": ("NSE_EQ|INE239A01024", "Nestle India Ltd"),
        "ULTRACEMCO": ("NSE_EQ|INE481G01011", "UltraTech Cement Ltd"),
        "GRASIM": ("NSE_EQ|INE047A01021", "Grasim Industries Ltd"),
        "BAJAJ-AUTO": ("NSE_EQ|INE917I01010", "Bajaj Auto Ltd"),
        "EICHERMOT": ("NSE_EQ|INE066A01021", "Eicher Motors Ltd"),
        "HEROMOTOCO": ("NSE_EQ|INE158A01026", "Hero MotoCorp Ltd"),
        "DRREDDY": ("NSE_EQ|INE089A01031", "Dr. Reddy's Laboratories Ltd"),
        "CIPLA": ("NSE_EQ|INE059A01026", "Cipla Ltd"),
        "DIVISLAB": ("NSE_EQ|INE361B01024", "Divi's Laboratories Ltd"),
        "APOLLOHOSP": ("NSE_EQ|INE437A01024", "Apollo Hospitals Enterprise Ltd"),
        "BPCL": ("NSE_EQ|INE029A01011", "Bharat Petroleum Corp Ltd"),
        "BAJAJFINSV": ("NSE_EQ|INE918I01026", "Bajaj Finserv Ltd"),
        "SBILIFE": ("NSE_EQ|INE123W01016", "SBI Life Insurance Company Ltd"),
        "HDFCLIFE": ("NSE_EQ|INE795G01014", "HDFC Life Insurance Company Ltd"),
        "TRENT": ("NSE_EQ|INE849A01020", "Trent Ltd"),
        "BEL": ("NSE_EQ|INE263A01024", "Bharat Electronics Ltd"),
        "HAL": ("NSE_EQ|INE066F01020", "Hindustan Aeronautics Ltd"),
        "TATAPOWER": ("NSE_EQ|INE245A01021", "Tata Power Company Ltd"),
        "SUZLON": ("NSE_EQ|INE040H01021", "Suzlon Energy Ltd"),
        "IRFC": ("NSE_EQ|INE053F01010", "Indian Railway Finance Corp Ltd"),
        "IRCTC": ("NSE_EQ|INE335Y01020", "Indian Railway Catering & Tourism Corp"),
        "JIOFIN": ("NSE_EQ|INE758E01017", "Jio Financial Services Ltd"),
        "BSE": ("NSE_EQ|INE118H01025", "BSE Ltd"),
        "TMPV": ("NSE_EQ|INE155A01022", "Tata Motors Passenger Vehicles Ltd"),
        "TMCV": ("NSE_EQ|INE155A01022", "Tata Motors Commercial Vehicles Ltd"),
        "TATAMOTORS": ("NSE_EQ|INE155A01022", "Tata Motors Ltd"),
        "ETERNAL": ("NSE_EQ|INE758T01015", "Eternal Ltd (formerly Zomato)"),
        "ZOMATO": ("NSE_EQ|INE758T01015", "Eternal Ltd (formerly Zomato)"),
    }

    def __init__(self, fallback: Optional[BaseMarketProvider] = None):
        self.fallback = fallback or IndianMarketProvider()
        self.api_key = settings.UPSTOX_API_KEY
        self.access_token = settings.UPSTOX_ACCESS_TOKEN
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop = None
        self._semaphore = asyncio.Semaphore(12)
        self._dynamic_cache: Dict[str, str] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client is None or self._client.is_closed or self._client_loop != current_loop:
            if self._client and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            self._client = httpx.AsyncClient(
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                timeout=6.0,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=15),
            )
            self._client_loop = current_loop
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if hasattr(self.fallback, "close"):
            await self.fallback.close()

    def _get_auth_headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    def _clean_symbol(self, symbol: str) -> str:
        s = symbol.strip().upper()
        if s.startswith("NSE:"):
            s = s[4:]
        elif s.startswith("BSE:"):
            s = s[4:]
        s = s.replace(".NS", "").replace(".BO", "")
        return s

    def _resolve_instrument_key(self, symbol: str) -> Optional[tuple[str, str]]:
        """Resolves symbol to (instrument_key, company_name)"""
        clean = self._clean_symbol(symbol)

        # Check indices
        if symbol in self.INDEX_INSTRUMENT_MAP:
            return self.INDEX_INSTRUMENT_MAP[symbol]
        if clean in self.INDEX_INSTRUMENT_MAP:
            return self.INDEX_INSTRUMENT_MAP[clean]

        # Check equities
        if clean in self.EQUITY_INSTRUMENT_MAP:
            return self.EQUITY_INSTRUMENT_MAP[clean]

        # Check dynamic cache
        if clean in self._dynamic_cache:
            return self._dynamic_cache[clean], clean

        # Direct instrument key format passed (e.g. NSE_EQ|... or NSE_INDEX|...)
        if "|" in symbol:
            return symbol, clean

        return None

    def _is_indian_market_open(self) -> bool:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.datetime.now(ist)
        if now.weekday() >= 5:
            return False
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    async def _fetch_quote_via_api(self, instrument_key: str, clean_symbol: str, company_name: str) -> Optional[StockQuote]:
        """Fetch quote via Upstox v2 market-quote API if access token is available."""
        if not self.access_token:
            return None

        url = f"{self.BASE_URL}/market-quote/quotes"
        params = {"instrument_key": instrument_key}
        try:
            client = await self._get_client()
            res = await client.get(url, headers=self._get_auth_headers(), params=params)
            if res.status_code == 200:
                data = res.json().get("data", {})
                quote_data = data.get(instrument_key.replace("|", ":")) or list(data.values())[0] if data else None
                if quote_data:
                    ohlc = quote_data.get("ohlc", {})
                    last_price = float(quote_data.get("last_price", 0.0))
                    prev_close = float(ohlc.get("close", 0.0))
                    change = last_price - prev_close if prev_close else 0.0
                    change_pct = (change / prev_close * 100) if prev_close else 0.0

                    return StockQuote(
                        symbol=clean_symbol,
                        company_name=company_name or clean_symbol,
                        exchange="NSE",
                        currency="INR",
                        current_price=round(last_price, 2),
                        change=round(change, 2),
                        change_percent=round(change_pct, 2),
                        open_price=ohlc.get("open"),
                        day_high=ohlc.get("high"),
                        day_low=ohlc.get("low"),
                        previous_close=prev_close,
                        volume=quote_data.get("volume"),
                        provider="Upstox API",
                    )
        except Exception as exc:
            logger.debug("Upstox auth API error for %s: %s", clean_symbol, exc)

        return None

    async def _fetch_quote_via_candles(self, instrument_key: str, clean_symbol: str, company_name: str) -> Optional[StockQuote]:
        """
        Fetch real-time quote via Upstox intraday 1-minute and daily candles.
        Requires zero authentication and provides live sub-second market data.
        """
        try:
            client = await self._get_client()
            encoded_key = urllib.parse.quote(instrument_key, safe="")

            # 1. Fetch live intraday 1-minute candles
            url_intraday = f"{self.BASE_URL}/historical-candle/intraday/{encoded_key}/1minute"
            res_intraday = await client.get(url_intraday)
            if res_intraday.status_code != 200:
                return None

            candles = res_intraday.json().get("data", {}).get("candles", [])
            if not candles:
                return None

            latest_candle = candles[0]
            current_price = float(latest_candle[4])  # close of latest minute

            # Open of today is first candle of the day (last element in reverse chronological list)
            open_price = float(candles[-1][1])
            day_high = max(float(c[2]) for c in candles)
            day_low = min(float(c[3]) for c in candles)
            total_vol = sum(int(c[5]) for c in candles if len(c) > 5 and c[5] is not None)

            # 2. Fetch daily candles for previous close
            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.datetime.now(ist).date()
            from_date = today - datetime.timedelta(days=7)
            url_daily = f"{self.BASE_URL}/historical-candle/{encoded_key}/day/{today.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}"

            prev_close = current_price
            res_daily = await client.get(url_daily)
            if res_daily.status_code == 200:
                daily_candles = res_daily.json().get("data", {}).get("candles", [])
                if daily_candles:
                    # If first daily candle date is today, prev close is second candle; otherwise first
                    first_dt = daily_candles[0][0].split("T")[0]
                    if first_dt == today.strftime("%Y-%m-%d") and len(daily_candles) > 1:
                        prev_close = float(daily_candles[1][4])
                    else:
                        prev_close = float(daily_candles[0][4])

            change = current_price - prev_close if prev_close else 0.0
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            return StockQuote(
                symbol=clean_symbol,
                company_name=company_name or clean_symbol,
                exchange="NSE",
                currency="INR",
                current_price=round(current_price, 2),
                change=round(change, 2),
                change_percent=round(change_pct, 2),
                open_price=round(open_price, 2) if open_price else None,
                day_high=round(day_high, 2) if day_high else None,
                day_low=round(day_low, 2) if day_low else None,
                previous_close=round(prev_close, 2) if prev_close else None,
                volume=total_vol if total_vol > 0 else None,
                provider="Upstox API (Live Feed)",
            )
        except Exception as exc:
            logger.debug("Upstox candle quote error for %s: %s", clean_symbol, exc)

        return None

    async def get_quote(self, symbol: str) -> Optional[StockQuote]:
        clean_symbol = self._clean_symbol(symbol)
        resolved = self._resolve_instrument_key(symbol)

        if resolved:
            instrument_key, company_name = resolved

            # Try authenticated API first if access token exists
            if self.access_token:
                quote = await self._fetch_quote_via_api(instrument_key, clean_symbol, company_name)
                if quote and quote.current_price > 0:
                    return quote

            # Try public intraday candle feed (no token required)
            quote = await self._fetch_quote_via_candles(instrument_key, clean_symbol, company_name)
            if quote and quote.current_price > 0:
                return quote

        # Fallback to secondary provider (IndianMarketProvider)
        return await self.fallback.get_quote(symbol)

    async def get_quotes(self, symbols: List[str]) -> List[StockQuote]:
        if not symbols:
            return []

        # 1. Batch API if token available
        clean_symbols = [self._clean_symbol(s) for s in symbols]
        instrument_map: Dict[str, tuple[str, str]] = {}
        missing_symbols: List[str] = []

        for s in symbols:
            clean = self._clean_symbol(s)
            res = self._resolve_instrument_key(s)
            if res:
                instrument_map[clean] = res
            else:
                missing_symbols.append(s)

        results: List[StockQuote] = []

        if self.access_token and instrument_map:
            try:
                keys = ",".join([key for key, _ in instrument_map.values()])
                url = f"{self.BASE_URL}/market-quote/quotes"
                client = await self._get_client()
                res = await client.get(url, headers=self._get_auth_headers(), params={"instrument_key": keys})
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    for raw_key, q_data in data.items():
                        norm_key = raw_key.replace(":", "|")
                        for clean, (ikey, cname) in instrument_map.items():
                            if ikey == norm_key:
                                ohlc = q_data.get("ohlc", {})
                                last_price = float(q_data.get("last_price", 0.0))
                                prev_close = float(ohlc.get("close", 0.0))
                                change = last_price - prev_close if prev_close else 0.0
                                change_pct = (change / prev_close * 100) if prev_close else 0.0
                                results.append(
                                    StockQuote(
                                        symbol=clean,
                                        company_name=cname,
                                        exchange="NSE",
                                        currency="INR",
                                        current_price=round(last_price, 2),
                                        change=round(change, 2),
                                        change_percent=round(change_pct, 2),
                                        open_price=ohlc.get("open"),
                                        day_high=ohlc.get("high"),
                                        day_low=ohlc.get("low"),
                                        previous_close=prev_close,
                                        volume=q_data.get("volume"),
                                        provider="Upstox API",
                                    )
                                )
                                break
            except Exception as exc:
                logger.debug("Upstox batch quotes error: %s", exc)

        # 2. Concurrently fetch via candle API for remaining mapped symbols
        fetched_syms = {q.symbol for q in results}
        unfetched = [s for s in symbols if self._clean_symbol(s) not in fetched_syms]

        if unfetched:
            tasks = [self.get_quote(s) for s in unfetched]
            fetched = await asyncio.gather(*tasks, return_exceptions=True)
            for q in fetched:
                if isinstance(q, StockQuote) and q.current_price > 0:
                    results.append(q)

        return results

    async def _fetch_single_index(self, symbol: str, instrument_key: str, name: str) -> Optional[IndexQuote]:
        try:
            client = await self._get_client()
            encoded_key = urllib.parse.quote(instrument_key, safe="")

            # Fetch intraday 1m candle for current value
            url = f"{self.BASE_URL}/historical-candle/intraday/{encoded_key}/1minute"
            res = await client.get(url)
            if res.status_code != 200:
                return None

            candles = res.json().get("data", {}).get("candles", [])
            if not candles:
                return None

            latest = candles[0]
            current_val = float(latest[4])
            open_val = float(candles[-1][1])
            high_val = max(float(c[2]) for c in candles)
            low_val = min(float(c[3]) for c in candles)

            # Daily candles for previous close
            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.datetime.now(ist).date()
            from_date = today - datetime.timedelta(days=7)
            url_daily = f"{self.BASE_URL}/historical-candle/{encoded_key}/day/{today.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}"

            prev_close = current_val
            res_daily = await client.get(url_daily)
            if res_daily.status_code == 200:
                daily_candles = res_daily.json().get("data", {}).get("candles", [])
                if daily_candles:
                    first_dt = daily_candles[0][0].split("T")[0]
                    if first_dt == today.strftime("%Y-%m-%d") and len(daily_candles) > 1:
                        prev_close = float(daily_candles[1][4])
                    else:
                        prev_close = float(daily_candles[0][4])

            change = current_val - prev_close if prev_close else 0.0
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            return IndexQuote(
                symbol=symbol,
                name=name,
                current_value=round(current_val, 2),
                change=round(change, 2),
                change_percent=round(change_pct, 2),
                open=round(open_val, 2) if open_val else None,
                high=round(high_val, 2) if high_val else None,
                low=round(low_val, 2) if low_val else None,
                previous_close=round(prev_close, 2) if prev_close else None,
                is_market_open=self._is_indian_market_open(),
            )
        except Exception as exc:
            logger.debug("Upstox index fetch error for %s: %s", symbol, exc)
            return None

    async def get_indices(self) -> List[IndexQuote]:
        indices_to_fetch = [
            ("^NSEI", "NSE_INDEX|Nifty 50", "NIFTY 50"),
            ("^BSESN", "BSE_INDEX|SENSEX", "SENSEX"),
            ("^NSEBANK", "NSE_INDEX|Nifty Bank", "NIFTY BANK"),
            ("^CNXIT", "NSE_INDEX|Nifty IT", "NIFTY IT"),
            ("NIFTY_MIDCAP_100.NS", "NSE_INDEX|NIFTY MIDCAP 100", "NIFTY MIDCAP 100"),
        ]

        tasks = [
            self._fetch_single_index(sym, key, name)
            for sym, key, name in indices_to_fetch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_indices = [r for r in results if isinstance(r, IndexQuote)]

        if len(valid_indices) >= 3:
            return valid_indices

        # Fallback if Upstox indices could not be fetched
        fallback_indices = await self.fallback.get_indices()
        return fallback_indices if fallback_indices else valid_indices

    async def get_market_overview(
        self, index_filter: Optional[str] = None, limit: int = 20
    ) -> MarketOverview:
        # Delegate to fallback provider (IndianMarketProvider) for official live NSE movers & indices
        return await self.fallback.get_market_overview(index_filter=index_filter, limit=limit)

    async def get_historical_candles(
        self, symbol: str, interval: str = "1d", range_period: str = "1mo"
    ) -> Optional[StockHistoryResponse]:
        resolved = self._resolve_instrument_key(symbol)
        if not resolved:
            return await self.fallback.get_historical_candles(symbol, interval, range_period)

        instrument_key, _ = resolved
        encoded_key = urllib.parse.quote(instrument_key, safe="")

        # Map interval and calculate date range
        ist = pytz.timezone("Asia/Kolkata")
        today = datetime.datetime.now(ist).date()

        days_map = {
            "1d": 1,
            "5d": 5,
            "1mo": 30,
            "3mo": 90,
            "6mo": 180,
            "1y": 365,
            "5y": 1825,
        }
        days = days_map.get(range_period, 30)
        from_date = today - datetime.timedelta(days=days)

        upstox_interval = "day"
        if interval in ["1m", "1minute"]:
            upstox_interval = "1minute"
        elif interval in ["30m", "30minute"]:
            upstox_interval = "30minute"
        elif interval in ["1wk", "week"]:
            upstox_interval = "week"
        elif interval in ["1mo", "month"]:
            upstox_interval = "month"

        try:
            client = await self._get_client()
            url = f"{self.BASE_URL}/historical-candle/{encoded_key}/{upstox_interval}/{today.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}"
            res = await client.get(url)
            if res.status_code == 200:
                raw_candles = res.json().get("data", {}).get("candles", [])
                if raw_candles:
                    candles = []
                    # Upstox returns newest first; reverse for chronological order
                    for c in reversed(raw_candles):
                        dt = datetime.datetime.fromisoformat(c[0])
                        candles.append(
                            CandleData(
                                timestamp=dt,
                                open=round(float(c[1]), 2),
                                high=round(float(c[2]), 2),
                                low=round(float(c[3]), 2),
                                close=round(float(c[4]), 2),
                                volume=int(c[5]) if len(c) > 5 and c[5] is not None else 0,
                            )
                        )
                    clean = self._clean_symbol(symbol)
                    return StockHistoryResponse(
                        symbol=clean,
                        interval=interval,
                        range=range_period,
                        candles=candles,
                    )
        except Exception as exc:
            logger.debug("Upstox historical candle error for %s: %s", symbol, exc)

        return await self.fallback.get_historical_candles(symbol, interval, range_period)

    async def search_symbols(self, query: str) -> List[StockSearchResult]:
        q = query.strip().upper()
        results: List[StockSearchResult] = []

        # 1. Match from pre-mapped equities
        for sym, (key, name) in self.EQUITY_INSTRUMENT_MAP.items():
            if q in sym or q in name.upper():
                results.append(
                    StockSearchResult(
                        symbol=sym,
                        name=name,
                        exchange="NSE",
                        instrument_type="EQUITY",
                    )
                )

        # 2. Match from pre-mapped indices
        for sym, (key, name) in self.INDEX_INSTRUMENT_MAP.items():
            if q in sym or q in name.upper():
                results.append(
                    StockSearchResult(
                        symbol=sym,
                        name=name,
                        exchange="NSE",
                        instrument_type="INDEX",
                    )
                )

        if results:
            return results[:15]

        # 3. Fallback to IndianMarketProvider search
        return await self.fallback.search_symbols(query)
