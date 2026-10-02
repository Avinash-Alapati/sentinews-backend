import asyncio
import datetime
import logging
import urllib.parse
from typing import Dict, List, Optional, Tuple
import httpx
import pytz

logger = logging.getLogger(__name__)

from app.cache.market_cache import market_cache
from app.integrations.market.base import BaseMarketProvider
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    MarketMover,
    CandleData,
    StockHistoryResponse,
    StockSearchResult,
)


from app.integrations.market.index_constituents import (
    matches_index_filter,
    get_constituent_company_name,
    NIFTY_50_SYMBOLS,
    NIFTY_100_SYMBOLS,
    NIFTY_500_SYMBOLS,
    NIFTY_MIDCAP_100_SYMBOLS,
    NIFTY_SMALLCAP_100_SYMBOLS,
    NIFTY_TOTAL_MARKET_SYMBOLS,
)


class IndianMarketProvider(BaseMarketProvider):
    """
    High-Performance Asynchronous Market Data Provider for Indian Equities (NSE/BSE)
    and Benchmark Indices with persistent connection pooling, semaphore-bounded
    concurrency, and live multi-exchange fallback.
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    }

    # Official NSE Real-Time Market Variations & Activity Endpoints
    NSE_GAINERS_URL = "https://www.nseindia.com/api/live-analysis-variations?index=gainers"
    NSE_LOSERS_URL = "https://www.nseindia.com/api/live-analysis-variations?index=loosers"
    NSE_LOSERS_FALLBACK_URL = "https://www.nseindia.com/api/live-analysis-variations?index=losers"
    NSE_VOLUME_GAINERS_URL = "https://www.nseindia.com/api/live-analysis-volume-gainers"
    NSE_MOST_ACTIVE_URL = "https://www.nseindia.com/api/live-analysis-most-active-securities?index=volume"
    NSE_MOST_ACTIVE_VALUE_URL = "https://www.nseindia.com/api/live-analysis-most-active-securities?index=value"
    SNAPSHOT_KEY_LIVE_MOVERS = "market:overview:live_movers"

    MAJOR_INDICES = [
    # =========================
    # NSE - BROAD MARKET
    # =========================
    {"symbol": "^NSEI", "name": "NIFTY 50"},
    {"symbol": "^NSMIDCP", "name": "NIFTY MIDCAP 100"},
    {"symbol": "^NSEMDCP50", "name": "NIFTY MIDCAP 50"},
    {"symbol": "^CNX100", "name": "NIFTY 100"},
    {"symbol": "^CNX200", "name": "NIFTY 200"},
    {"symbol": "^CRSLDX", "name": "NIFTY 500"},
    {"symbol": "^CNXSC", "name": "NIFTY SMALLCAP 100"},

    # =========================
    # NSE - SECTORAL
    # =========================
    {"symbol": "^NSEBANK", "name": "NIFTY BANK"},
    {"symbol": "^CNXIT", "name": "NIFTY IT"},
    {"symbol": "^CNXAUTO", "name": "NIFTY AUTO"},
    {"symbol": "^CNXPHARMA", "name": "NIFTY PHARMA"},
    {"symbol": "^CNXFMCG", "name": "NIFTY FMCG"},
    {"symbol": "^CNXMETAL", "name": "NIFTY METAL"},
    {"symbol": "^CNXREALTY", "name": "NIFTY REALTY"},
    {"symbol": "^CNXENERGY", "name": "NIFTY ENERGY"},
    {"symbol": "^CNXMEDIA", "name": "NIFTY MEDIA"},
    {"symbol": "^CNXINFRA", "name": "NIFTY INFRA"},
    {"symbol": "^CNXPSUBANK", "name": "NIFTY PSU BANK"},
    {"symbol": "^CNXPSE", "name": "NIFTY PSE"},
    {"symbol": "^CNXSERVICE", "name": "NIFTY SERVICES SECTOR"},

    # =========================
    # NSE - FINANCIAL INDICES
    # =========================
    {"symbol": "^CNXFIN", "name": "NIFTY FINANCIAL SERVICES"},

    # =========================
    # NSE - VOLATILITY
    # =========================
    {"symbol": "^INDIAVIX", "name": "INDIA VIX"},

    # =========================
    # BSE - MAJOR INDICES
    # =========================
    {"symbol": "^BSESN", "name": "SENSEX"},
    {"symbol": "BSE-100.BO", "name": "BSE 100"},
    {"symbol": "BSE-200.BO", "name": "BSE 200"},
    {"symbol": "BSE-500.BO", "name": "BSE 500"},

    # =========================
    # BSE - SECTORAL
    # =========================
    {"symbol": "BSE-IT.BO", "name": "BSE IT"},
    {"symbol": "BSE-AUTO.BO", "name": "BSE AUTO"},
    {"symbol": "BSE-FMCG.BO", "name": "BSE FMCG"},
    {"symbol": "BSE-METAL.BO", "name": "BSE METAL"},
    {"symbol": "BSE-REALTY.BO", "name": "BSE REALTY"},
    {"symbol": "BSE-POWER.BO", "name": "BSE POWER"},
    {"symbol": "BSE-OILGAS.BO", "name": "BSE OIL & GAS"},
    {"symbol": "BSE-TECK.BO", "name": "BSE TECK"},
]

    STOCK_NAME_MAP: Dict[str, str] = {
        "RELIANCE": "Reliance Industries Ltd",
        "TCS": "Tata Consultancy Services Ltd",
        "HDFCBANK": "HDFC Bank Ltd",
        "INFY": "Infosys Ltd",
        "ICICIBANK": "ICICI Bank Ltd",
        "BHARTIARTL": "Bharti Airtel Ltd",
        "SBIN": "State Bank of India",
        "ITC": "ITC Ltd",
        "HINDUNILVR": "Hindustan Unilever Ltd",
        "LT": "Larsen & Toubro Ltd",
        "TMPV": "Tata Motors Passenger Vehicles Ltd",
        "TMCV": "Tata Motors Commercial Vehicles Ltd",
        "TATAMOTORS": "Tata Motors Ltd",
        "BAJFINANCE": "Bajaj Finance Ltd",
        "SUNPHARMA": "Sun Pharmaceutical Ind Ltd",
        "MARUTI": "Maruti Suzuki India Ltd",
        "AXISBANK": "Axis Bank Ltd",
        "TITAN": "Titan Company Ltd",
        "WIPRO": "Wipro Ltd",
        "ETERNAL": "Eternal Ltd (formerly Zomato)",
        "ZOMATO": "Eternal Ltd (formerly Zomato)",
        "ADANIENT": "Adani Enterprises Ltd",
        "POWERGRID": "Power Grid Corp of India",
        "M&M": "Mahindra & Mahindra Ltd",
        "NTPC": "NTPC Ltd",
        "ONGC": "Oil & Natural Gas Corp Ltd",
        "COALINDIA": "Coal India Ltd",
        "TATASTEEL": "Tata Steel Ltd",
        "JSWSTEEL": "JSW Steel Ltd",
        "HINDALCO": "Hindalco Industries Ltd",
        "HCLTECH": "HCL Technologies Ltd",
        "TECHM": "Tech Mahindra Ltd",
        "ASIANPAINT": "Asian Paints Ltd",
        "NESTLEIND": "Nestle India Ltd",
        "ULTRACEMCO": "UltraTech Cement Ltd",
        "GRASIM": "Grasim Industries Ltd",
        "BAJAJ-AUTO": "Bajaj Auto Ltd",
        "EICHERMOT": "Eicher Motors Ltd",
        "HEROMOTOCO": "Hero MotoCorp Ltd",
        "HEROMOTORS": "Hero MotoCorp Ltd",
        "DRREDDY": "Dr. Reddy's Laboratories Ltd",
        "CIPLA": "Cipla Ltd",
        "DIVISLAB": "Divi's Laboratories Ltd",
        "APOLLOHOSP": "Apollo Hospitals Enterprise Ltd",
        "BPCL": "Bharat Petroleum Corp Ltd",
        "BAJAJFINSV": "Bajaj Finserv Ltd",
        "SBILIFE": "SBI Life Insurance Company Ltd",
        "HDFCLIFE": "HDFC Life Insurance Company Ltd",
        "TRENT": "Trent Ltd",
        "BEL": "Bharat Electronics Ltd",
        "HAL": "Hindustan Aeronautics Ltd",
        "TATAPOWER": "Tata Power Company Ltd",
        "SUZLON": "Suzlon Energy Ltd",
        "IRFC": "Indian Railway Finance Corp Ltd",
        "IRCTC": "Indian Railway Catering & Tourism Corp",
        "JIOFIN": "Jio Financial Services Ltd",
        "BSE": "BSE Ltd",
        "MAXHEALTH": "Max Healthcare Institute Ltd",
        "KAYNES": "Kaynes Technology India Ltd",
        "MFSL": "Max Financial Services Ltd",
        "SUPREMEIND": "Supreme Industries Ltd",
        "MAHABANK": "Bank of Maharashtra",
        "FORTIS": "Fortis Healthcare Ltd",
        "PAYTM": "One97 Communications Ltd (Paytm)",
        "POLICYBZR": "PB Fintech Ltd (PolicyBazaar)",
        "MCX": "Multi Commodity Exchange of India Ltd",
        "PCJEWELLER": "PC Jeweller Ltd",
        "IDEA": "Vodafone Idea Ltd",
        "OLAELEC": "Ola Electric Mobility Ltd",
        "MOTISONS": "Motisons Jewellers Ltd",
        "VEDL": "Vedanta Ltd",
        "YESBANK": "Yes Bank Ltd",
        "NHPC": "NHPC Ltd",
        "IOC": "Indian Oil Corp Ltd",
        "GAIL": "GAIL (India) Ltd",
        "DLF": "DLF Ltd",
        "INDUSINDBK": "IndusInd Bank Ltd",
        "SHREECEM": "Shree Cement Ltd",
        "PIDILITIND": "Pidilite Industries Ltd",
        "SIEMENS": "Siemens Ltd",
        "HAVELLS": "Havells India Ltd",
        "DABUR": "Dabur India Ltd",
        "GODREJCP": "Godrej Consumer Products Ltd",
        "AMBUJACEM": "Ambuja Cements Ltd",
        "BANKBARODA": "Bank of Baroda",
        "PNB": "Punjab National Bank",
        "CANBK": "Canara Bank",
        "IDFCFIRSTB": "IDFC First Bank Ltd",
        "FEDERALBNK": "Federal Bank Ltd",
    }

    POPULAR_INDIAN_STOCKS = [
        {"symbol": "RELIANCE.NS", "name": "Reliance Industries Ltd", "clean": "RELIANCE"},
        {"symbol": "TCS.NS", "name": "Tata Consultancy Services Ltd", "clean": "TCS"},
        {"symbol": "HDFCBANK.NS", "name": "HDFC Bank Ltd", "clean": "HDFCBANK"},
        {"symbol": "INFY.NS", "name": "Infosys Ltd", "clean": "INFY"},
        {"symbol": "ICICIBANK.NS", "name": "ICICI Bank Ltd", "clean": "ICICIBANK"},
        {"symbol": "BHARTIARTL.NS", "name": "Bharti Airtel Ltd", "clean": "BHARTIARTL"},
        {"symbol": "SBIN.NS", "name": "State Bank of India", "clean": "SBIN"},
        {"symbol": "ITC.NS", "name": "ITC Ltd", "clean": "ITC"},
        {"symbol": "HINDUNILVR.NS", "name": "Hindustan Unilever Ltd", "clean": "HINDUNILVR"},
        {"symbol": "LT.NS", "name": "Larsen & Toubro Ltd", "clean": "LT"},
        {"symbol": "TMPV.NS", "name": "Tata Motors Passenger Vehicles Ltd", "clean": "TMPV"},
        {"symbol": "TMCV.NS", "name": "Tata Motors Commercial Vehicles Ltd", "clean": "TMCV"},
        {"symbol": "BAJFINANCE.NS", "name": "Bajaj Finance Ltd", "clean": "BAJFINANCE"},
        {"symbol": "SUNPHARMA.NS", "name": "Sun Pharmaceutical Ind Ltd", "clean": "SUNPHARMA"},
        {"symbol": "MARUTI.NS", "name": "Maruti Suzuki India Ltd", "clean": "MARUTI"},
        {"symbol": "AXISBANK.NS", "name": "Axis Bank Ltd", "clean": "AXISBANK"},
        {"symbol": "TITAN.NS", "name": "Titan Company Ltd", "clean": "TITAN"},
        {"symbol": "WIPRO.NS", "name": "Wipro Ltd", "clean": "WIPRO"},
        {"symbol": "ETERNAL.NS", "name": "Eternal Ltd (formerly Zomato)", "clean": "ETERNAL"},
        {"symbol": "ADANIENT.NS", "name": "Adani Enterprises Ltd", "clean": "ADANIENT"},
        {"symbol": "ADANIPORTS.NS", "name": "Adani Ports & SEZ Ltd", "clean": "ADANIPORTS"},
        {"symbol": "POWERGRID.NS", "name": "Power Grid Corp of India", "clean": "POWERGRID"},
        {"symbol": "M&M.NS", "name": "Mahindra & Mahindra Ltd", "clean": "M&M"},
        {"symbol": "NTPC.NS", "name": "NTPC Ltd", "clean": "NTPC"},
        {"symbol": "ONGC.NS", "name": "Oil & Natural Gas Corp Ltd", "clean": "ONGC"},
        {"symbol": "COALINDIA.NS", "name": "Coal India Ltd", "clean": "COALINDIA"},
        {"symbol": "TATASTEEL.NS", "name": "Tata Steel Ltd", "clean": "TATASTEEL"},
        {"symbol": "JSWSTEEL.NS", "name": "JSW Steel Ltd", "clean": "JSWSTEEL"},
        {"symbol": "HINDALCO.NS", "name": "Hindalco Industries Ltd", "clean": "HINDALCO"},
        {"symbol": "HCLTECH.NS", "name": "HCL Technologies Ltd", "clean": "HCLTECH"},
        {"symbol": "TECHM.NS", "name": "Tech Mahindra Ltd", "clean": "TECHM"},
        {"symbol": "ASIANPAINT.NS", "name": "Asian Paints Ltd", "clean": "ASIANPAINT"},
        {"symbol": "NESTLEIND.NS", "name": "Nestle India Ltd", "clean": "NESTLEIND"},
        {"symbol": "BRITANNIA.NS", "name": "Britannia Industries Ltd", "clean": "BRITANNIA"},
        {"symbol": "ULTRACEMCO.NS", "name": "UltraTech Cement Ltd", "clean": "ULTRACEMCO"},
        {"symbol": "GRASIM.NS", "name": "Grasim Industries Ltd", "clean": "GRASIM"},
        {"symbol": "BAJAJ-AUTO.NS", "name": "Bajaj Auto Ltd", "clean": "BAJAJ-AUTO"},
        {"symbol": "EICHERMOT.NS", "name": "Eicher Motors Ltd", "clean": "EICHERMOT"},
        {"symbol": "HEROMOTOCO.NS", "name": "Hero MotoCorp Ltd", "clean": "HEROMOTOCO"},
        {"symbol": "DRREDDY.NS", "name": "Dr. Reddy's Laboratories Ltd", "clean": "DRREDDY"},
        {"symbol": "CIPLA.NS", "name": "Cipla Ltd", "clean": "CIPLA"},
        {"symbol": "DIVISLAB.NS", "name": "Divi's Laboratories Ltd", "clean": "DIVISLAB"},
        {"symbol": "APOLLOHOSP.NS", "name": "Apollo Hospitals Enterprise Ltd", "clean": "APOLLOHOSP"},
        {"symbol": "BPCL.NS", "name": "Bharat Petroleum Corp Ltd", "clean": "BPCL"},
        {"symbol": "BAJAJFINSV.NS", "name": "Bajaj Finserv Ltd", "clean": "BAJAJFINSV"},
        {"symbol": "SBILIFE.NS", "name": "SBI Life Insurance Company Ltd", "clean": "SBILIFE"},
        {"symbol": "HDFCLIFE.NS", "name": "HDFC Life Insurance Company Ltd", "clean": "HDFCLIFE"},
        {"symbol": "INDUSINDBK.NS", "name": "IndusInd Bank Ltd", "clean": "INDUSINDBK"},
        {"symbol": "KOTAKBANK.NS", "name": "Kotak Mahindra Bank Ltd", "clean": "KOTAKBANK"},
        {"symbol": "SHRIRAMFIN.NS", "name": "Shriram Finance Ltd", "clean": "SHRIRAMFIN"},
        {"symbol": "TATACONSUM.NS", "name": "Tata Consumer Products Ltd", "clean": "TATACONSUM"},
        {"symbol": "TRENT.NS", "name": "Trent Ltd", "clean": "TRENT"},
        {"symbol": "BEL.NS", "name": "Bharat Electronics Ltd", "clean": "BEL"},
        {"symbol": "HAL.NS", "name": "Hindustan Aeronautics Ltd", "clean": "HAL"},
        {"symbol": "TATAPOWER.NS", "name": "Tata Power Company Ltd", "clean": "TATAPOWER"},
        {"symbol": "SUZLON.NS", "name": "Suzlon Energy Ltd", "clean": "SUZLON"},
        {"symbol": "IRFC.NS", "name": "Indian Railway Finance Corp Ltd", "clean": "IRFC"},
        {"symbol": "IRCTC.NS", "name": "Indian Railway Catering & Tourism Corp", "clean": "IRCTC"},
        {"symbol": "JIOFIN.NS", "name": "Jio Financial Services Ltd", "clean": "JIOFIN"},
        {"symbol": "BSE.NS", "name": "BSE Ltd", "clean": "BSE"},
    ]

    NAME_LOOKUP = {s["clean"]: s["name"] for s in POPULAR_INDIAN_STOCKS}

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._sem_loop = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._semaphore is None or self._sem_loop != current_loop:
            self._semaphore = asyncio.Semaphore(20)
            self._sem_loop = current_loop
        return self._semaphore

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns a persistent, connection-pooled AsyncClient bound to current event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if (
            self._client is None
            or self._client.is_closed
            or self._client_loop != current_loop
        ):
            if self._client and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            limits = httpx.Limits(
                max_connections=100,
                max_keepalive_connections=30,
                keepalive_expiry=60.0,
            )
            from app.infrastructure.observability.http_tracer import instrument_httpx_client
            self._client = httpx.AsyncClient(
                headers=self.HEADERS,
                limits=limits,
                timeout=10.0,
                follow_redirects=True,
            )
            instrument_httpx_client(self._client)
            self._client_loop = current_loop
        return self._client

    async def close(self):
        """Closes the underlying HTTP connection pool on shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    COMMON_ALIASES = {
        # Demerged, renamed, and commonly queried Indian equities
        "TATAMOTORS": "TMPV.NS",
        "TATA_MOTORS": "TMPV.NS",
        "TMPV": "TMPV.NS",
        "TMCV": "TMCV.NS",
        "ZOMATO": "ETERNAL.NS",
        "ETERNAL": "ETERNAL.NS",
        # Banking & Financial Services
        "BANK": "^NSEBANK",
        "BANKNIFTY": "^NSEBANK",
        "NIFTYBANK": "^NSEBANK",
        "NIFTY_BANK": "^NSEBANK",
        "PSU_BANK": "^CNXPSUBANK",
        "NIFTY_PSU_BANK": "^CNXPSUBANK",
        "PVT_BANK": "NIFTY_PVT_BANK.NS",
        "PRIVATE_BANK": "NIFTY_PVT_BANK.NS",
        "NIFTY_PVT_BANK": "NIFTY_PVT_BANK.NS",
        "FINANCE": "NIFTY_FIN_SERVICE.NS",
        "FINANCIAL_SERVICES": "NIFTY_FIN_SERVICE.NS",
        "NIFTY_FIN_SERVICE": "NIFTY_FIN_SERVICE.NS",
        # Technology & Media
        "IT": "^CNXIT",
        "CNXIT": "^CNXIT",
        "NIFTY_IT": "^CNXIT",
        "MEDIA": "^CNXMEDIA",
        "NIFTY_MEDIA": "^CNXMEDIA",
        # Auto & Industrials
        "AUTO": "^CNXAUTO",
        "CNXAUTO": "^CNXAUTO",
        "NIFTY_AUTO": "^CNXAUTO",
        "INFRA": "^CNXINFRA",
        "INFRASTRUCTURE": "^CNXINFRA",
        "NIFTY_INFRA": "^CNXINFRA",
        # Healthcare & Pharma
        "PHARMA": "^CNXPHARMA",
        "CNXPHARMA": "^CNXPHARMA",
        "NIFTY_PHARMA": "^CNXPHARMA",
        "HEALTHCARE": "NIFTY_HEALTHCARE.NS",
        "NIFTY_HEALTHCARE": "NIFTY_HEALTHCARE.NS",
        # FMCG & Consumer Durables
        "FMCG": "^CNXFMCG",
        "CNXFMCG": "^CNXFMCG",
        "NIFTY_FMCG": "^CNXFMCG",
        "CONSUMER_DURABLES": "NIFTY_CONSR_DURBL.NS",
        "NIFTY_CONSR_DURBL": "NIFTY_CONSR_DURBL.NS",
        # Metals, Energy, Oil & Gas, Realty
        "METAL": "^CNXMETAL",
        "NIFTY_METAL": "^CNXMETAL",
        "ENERGY": "^CNXENERGY",
        "NIFTY_ENERGY": "^CNXENERGY",
        "OIL_AND_GAS": "NIFTY_OIL_AND_GAS.NS",
        "OIL_GAS": "NIFTY_OIL_AND_GAS.NS",
        "NIFTY_OIL_AND_GAS": "NIFTY_OIL_AND_GAS.NS",
        "REALTY": "^CNXREALTY",
        "NIFTY_REALTY": "^CNXREALTY",
        "CNXREALTY": "^CNXREALTY",
        # Broad & Mid/Small Cap Indices
        "NIFTY_MIDCAP_100": "NIFTY_MIDCAP_100.NS",
        "BSE-100": "BSE-100.BO",
        "BSE-200": "BSE-200.BO",
        "BSE-500": "BSE-500.BO",
        "BSE-MIDCAP": "MIDSEL.BO",
        "BSE-SMLCAP": "SMLSEL.BO",
    }

    def _lookup_company_name(self, symbol: str) -> str:
        clean = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        cname = get_constituent_company_name(clean)
        if cname:
            return cname
        if clean in self.STOCK_NAME_MAP:
            return self.STOCK_NAME_MAP[clean]
        for s in self.POPULAR_INDIAN_STOCKS:
            if s["clean"] == clean:
                return s["name"]
        return clean

    def _normalize_ticker(self, symbol: str) -> str:
        s = symbol.strip().upper()
        if s in self.COMMON_ALIASES:
            return self.COMMON_ALIASES[s]
        if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
            return s
        if s.startswith("NSE:"):
            return f"{s[4:]}.NS"
        if s.startswith("BSE:"):
            return f"{s[4:]}.BO"
        # Pure numeric ticker is a BSE security scrip code (e.g. 500325)
        if s.isdigit():
            return f"{s}.BO"
        # Default Indian equity to NSE
        return f"{s}.NS"

    def _is_indian_market_open(self) -> bool:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.datetime.now(ist)
        # Saturday=5, Sunday=6
        if now.weekday() >= 5:
            return False
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    async def _fetch_quote_for_ticker(
        self, client: httpx.AsyncClient, ticker: str, requested_symbol: Optional[str] = None
    ) -> Optional[StockQuote]:
        encoded_ticker = urllib.parse.quote(ticker)
        # Fast, low-bandwidth chart call containing complete real-time meta metrics
        url1 = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?interval=1d&range=5d"

        try:
            res = await client.get(url1)
            if res.status_code == 404:
                return None
            if res.status_code != 200 or not res.json().get("chart", {}).get("result"):
                url2 = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?interval=1d&range=5d"
                res = await client.get(url2)

            if res.status_code == 200:
                data = res.json()
                results = data.get("chart", {}).get("result", [])
                if not results:
                    return None

                item = results[0]
                meta = item.get("meta", {})

                price = float(meta.get("regularMarketPrice") or meta.get("fulldayPrice") or 0.0)
                day_high = float(meta.get("regularMarketDayHigh") or 0.0)
                day_low = float(meta.get("regularMarketDayLow") or 0.0)
                fifty_two_high = float(meta.get("fiftyTwoWeekHigh") or 0.0)
                fifty_two_low = float(meta.get("fiftyTwoWeekLow") or 0.0)
                meta_vol = meta.get("regularMarketVolume")

                # Extract indicators if available
                quote_indicators = item.get("indicators", {}).get("quote", [{}])[0]
                opens = [o for o in quote_indicators.get("open", []) if o is not None]
                open_price = float(opens[-1]) if opens else None
                highs = [h for h in quote_indicators.get("high", []) if h is not None]
                if not day_high and highs:
                    day_high = float(max(highs))
                lows = [l for l in quote_indicators.get("low", []) if l is not None]
                if not day_low and lows:
                    day_low = float(min(lows))
                volumes = [v for v in quote_indicators.get("volume", []) if v is not None]
                volume = sum(volumes) if volumes else (int(meta_vol) if meta_vol else None)
                closes = [float(c) for c in quote_indicators.get("close", []) if c is not None and float(c) > 0]

                meta_change = meta.get("fulldayChange") or meta.get("regularMarketChange")
                meta_change_pct = meta.get("fulldayChangePercent") or meta.get("regularMarketChangePercent")

                prev_close = None
                change = None
                change_pct = None
                volume = int(volumes[-1]) if volumes else (int(meta_vol) if meta_vol else None)

                if meta_change is not None and abs(float(meta_change)) > 0.0001:
                    change = float(meta_change)
                    if meta_change_pct is not None:
                        change_pct = float(meta_change_pct)
                    else:
                        prev_close_calc = price - change
                        change_pct = (change / prev_close_calc * 100) if prev_close_calc else 0.0
                    prev_close = price - change
                elif len(closes) >= 2:
                    last_close = closes[-1]
                    prev_close_val = closes[-2]
                    if prev_close_val > 0:
                        prev_close = prev_close_val
                        change = (price or last_close) - prev_close
                        change_pct = (change / prev_close) * 100
                elif meta.get("chartPreviousClose") and abs(float(meta.get("chartPreviousClose")) - price) > 0.0001:
                    prev_close = float(meta.get("chartPreviousClose"))
                    change = price - prev_close
                    change_pct = (change / prev_close) * 100
                elif meta.get("previousClose") and abs(float(meta.get("previousClose")) - price) > 0.0001:
                    prev_close = float(meta.get("previousClose"))
                    change = price - prev_close
                    change_pct = (change / prev_close) * 100

                if change is None:
                    change = 0.0
                if change_pct is None:
                    change_pct = 0.0
                if prev_close is None:
                    prev_close = price - change if price else price

                clean_sym = requested_symbol.strip().upper().replace(".NS", "").replace(".BO", "") if requested_symbol else ticker.replace(".NS", "").replace(".BO", "")
                exchange = "BSE" if ticker.endswith(".BO") or meta.get("exchangeName") == "BSE" else "NSE"

                # Extract company name
                comp_name = meta.get("longName") or meta.get("shortName") or self._lookup_company_name(clean_sym)
                if comp_name == clean_sym or not comp_name:
                    comp_name = self._lookup_company_name(clean_sym)
                comp_name = meta.get("longName") or meta.get("shortName") or clean_sym
                if comp_name == clean_sym or len(comp_name) <= 3:
                    comp_name = self.NAME_LOOKUP.get(clean_sym, clean_sym)

                return StockQuote(
                    symbol=clean_sym,
                    company_name=comp_name,
                    exchange=exchange,
                    currency=meta.get("currency", "INR"),
                    current_price=round(price, 2),
                    change=round(change, 2),
                    change_percent=round(change_pct, 2),
                    open_price=round(open_price, 2) if open_price else None,
                    day_high=round(day_high, 2) if day_high else None,
                    day_low=round(day_low, 2) if day_low else None,
                    previous_close=round(prev_close, 2) if prev_close else None,
                    volume=volume,
                    fifty_two_week_high=round(fifty_two_high, 2) if fifty_two_high else None,
                    fifty_two_week_low=round(fifty_two_low, 2) if fifty_two_low else None,
                    provider="NSE/BSE Real-Time Feed",
                )
        except Exception as exc:
            logger.debug("Error fetching quote for ticker '%s': %s", ticker, exc)
            return None

        return None

    async def _fetch_quote_with_fallback(
        self, client: httpx.AsyncClient, ticker: str, requested_symbol: Optional[str] = None
    ) -> Optional[StockQuote]:
        async with self._get_semaphore():
            quote = await self._fetch_quote_for_ticker(client, ticker, requested_symbol=requested_symbol)
            # Fallback to .BO if .NS was not found
            if not quote and ticker.endswith(".NS"):
                bse_ticker = ticker.replace(".NS", ".BO")
                quote = await self._fetch_quote_for_ticker(client, bse_ticker, requested_symbol=requested_symbol)
            return quote

    async def get_quote(self, symbol: str) -> Optional[StockQuote]:
        ticker = self._normalize_ticker(symbol)
        client = await self._get_client()
        return await self._fetch_quote_with_fallback(client, ticker, requested_symbol=symbol)

    async def get_quotes(self, symbols: List[str]) -> List[StockQuote]:
        client = await self._get_client()
        tasks = [
            self._fetch_quote_with_fallback(client, self._normalize_ticker(s), requested_symbol=s)
            for s in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        quotes = [r for r in results if isinstance(r, StockQuote)]
        return quotes

    async def _fetch_index(self, client: httpx.AsyncClient, symbol: str, name: str) -> Optional[IndexQuote]:
        encoded_symbol = urllib.parse.quote(symbol)
        url1 = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?interval=1d&range=5d"
        try:
            res = await client.get(url1)
            if res.status_code != 200 or not res.json().get("chart", {}).get("result"):
                url2 = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?interval=1d&range=5d"
                res = await client.get(url2)

            if res.status_code == 200:
                data = res.json()
                results = data.get("chart", {}).get("result", [])
                if results:
                    item = results[0]
                    meta = item.get("meta", {})
                    price = float(meta.get("regularMarketPrice") or meta.get("fulldayPrice") or 0.0)
                    day_high = float(meta.get("regularMarketDayHigh") or 0.0)
                    day_low = float(meta.get("regularMarketDayLow") or 0.0)

                    quote_indicators = item.get("indicators", {}).get("quote", [{}])[0]
                    opens = [o for o in quote_indicators.get("open", []) if o is not None]
                    open_price = float(opens[-1]) if opens else None
                    highs = [h for h in quote_indicators.get("high", []) if h is not None]
                    if not day_high and highs:
                        day_high = float(max(highs))
                    lows = [l for l in quote_indicators.get("low", []) if l is not None]
                    if not day_low and lows:
                        day_low = float(min(lows))
                    closes = [float(c) for c in quote_indicators.get("close", []) if c is not None and float(c) > 0]

                    meta_change = meta.get("fulldayChange") or meta.get("regularMarketChange")
                    meta_change_pct = meta.get("fulldayChangePercent") or meta.get("regularMarketChangePercent")

                    prev_close = None
                    change = None
                    change_pct = None

                    if meta_change is not None and abs(float(meta_change)) > 0.0001:
                        change = float(meta_change)
                        if meta_change_pct is not None:
                            change_pct = float(meta_change_pct)
                        else:
                            prev_close_calc = price - change
                            change_pct = (change / prev_close_calc * 100) if prev_close_calc else 0.0
                        prev_close = price - change
                    elif len(closes) >= 2:
                        last_close = closes[-1]
                        prev_close_val = closes[-2]
                        if prev_close_val > 0:
                            prev_close = prev_close_val
                            change = (price or last_close) - prev_close
                            change_pct = (change / prev_close) * 100
                    elif meta.get("chartPreviousClose") and abs(float(meta.get("chartPreviousClose")) - price) > 0.0001:
                        prev_close = float(meta.get("chartPreviousClose"))
                        change = price - prev_close
                        change_pct = (change / prev_close) * 100
                    elif meta.get("previousClose") and abs(float(meta.get("previousClose")) - price) > 0.0001:
                        prev_close = float(meta.get("previousClose"))
                        change = price - prev_close
                        change_pct = (change / prev_close) * 100

                    if change is None:
                        change = 0.0
                    if change_pct is None:
                        change_pct = 0.0
                    if prev_close is None:
                        prev_close = price - change if price else price

                    return IndexQuote(
                        symbol=symbol,
                        name=name,
                        current_value=round(price, 2),
                        change=round(change, 2),
                        change_percent=round(change_pct, 2),
                        open=round(open_price, 2) if open_price else None,
                        high=round(day_high, 2) if day_high else None,
                        low=round(day_low, 2) if day_low else None,
                        previous_close=round(prev_close, 2) if prev_close else None,
                        is_market_open=self._is_indian_market_open(),
                    )
        except Exception as exc:
            logger.debug("Error fetching index '%s': %s", symbol, exc)
        return None

    async def get_indices(self) -> List[IndexQuote]:
        client = await self._get_client()
        tasks = [
            self._fetch_index(client, item["symbol"], item["name"])
            for item in self.MAJOR_INDICES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        indices = [r for r in results if isinstance(r, IndexQuote)]
        return indices

    async def _fetch_live_nse_movers(
        self, index_filter: Optional[str] = None, limit: int = 20
    ) -> Tuple[List[MarketMover], List[MarketMover], List[MarketMover]]:
        """
        Queries official NSE real-time analysis variation, volume gainers, and activity endpoints:
        - Gainers: https://www.nseindia.com/api/live-analysis-variations?index=gainers
        - Losers: https://www.nseindia.com/api/live-analysis-variations?index=loosers
        - Volume Gainers: https://www.nseindia.com/api/live-analysis-volume-gainers
        - Most Active Volume: https://www.nseindia.com/api/live-analysis-most-active-securities?index=volume
        - Most Active Value: https://www.nseindia.com/api/live-analysis-most-active-securities?index=value

        Parses ALL groups ('allSec', 'SecGtr20', 'SecLwr20', 'FOSec', 'NIFTYNEXT50', 'NIFTY', 'BANKNIFTY')
        and filters by index (Nifty 50, Nifty 500, Midcap 100, Smallcap 100, Total Market) without skipping any percentage tiers.
        """
        client = await self._get_client()

        async def fetch_json(url: str):
            try:
                res = await client.get(url, timeout=5.0)
                if res.status_code == 200:
                    return res.json()
            except Exception as e:
                logger.debug("NSE mover fetch error for %s: %s", url, e)
            return None

        # Fetch gainers, losers, volume gainers, and most active concurrently
        g_data, l_data, vg_data, m_data, mv_data = await asyncio.gather(
            fetch_json(self.NSE_GAINERS_URL),
            fetch_json(self.NSE_LOSERS_URL),
            fetch_json(self.NSE_VOLUME_GAINERS_URL),
            fetch_json(self.NSE_MOST_ACTIVE_URL),
            fetch_json(self.NSE_MOST_ACTIVE_VALUE_URL),
            return_exceptions=True,
        )

        # Fallback for losers if loosers returned None or error
        if not l_data or isinstance(l_data, Exception) or (isinstance(l_data, dict) and "Missing" in str(l_data.get("data", ""))):
            l_data = await fetch_json(self.NSE_LOSERS_FALLBACK_URL)

        all_gainers: Dict[str, MarketMover] = {}
        all_losers: Dict[str, MarketMover] = {}
        all_active: Dict[str, MarketMover] = {}

        # 1. Parse ALL variations groups for gainers
        # Groups: allSec, SecGtr20, SecLwr20, FOSec, NIFTYNEXT50, NIFTY, BANKNIFTY
        variation_groups = ["allSec", "SecGtr20", "SecLwr20", "FOSec", "NIFTYNEXT50", "NIFTY", "BANKNIFTY"]

        if g_data and not isinstance(g_data, Exception) and isinstance(g_data, dict):
            for group in variation_groups:
                group_data = g_data.get(group, {})
                items = group_data.get("data", []) if isinstance(group_data, dict) else (group_data if isinstance(group_data, list) else [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sym = str(item.get("symbol", "")).strip().upper()
                    if not sym or sym in all_gainers or not matches_index_filter(sym, index_filter):
                        continue
                    ltp = float(item.get("ltp") or item.get("lastPrice") or 0.0)
                    prev = float(item.get("prev_price") or item.get("previousClose") or 0.0)
                    pct = float(item.get("perChange") or item.get("pChange") or 0.0)
                    chg = round(ltp - prev, 2) if prev else float(item.get("net_price") or item.get("change") or 0.0)
                    vol = int(item.get("trade_quantity") or item.get("totalTradedVolume") or item.get("quantityTraded") or 0)
                    if pct > 0 and ltp > 0:
                        name = self._lookup_company_name(sym)
                        all_gainers[sym] = MarketMover(
                            symbol=sym,
                            company_name=name,
                            exchange="NSE",
                            current_price=round(ltp, 2),
                            change=round(chg, 2),
                            change_percent=round(pct, 2),
                            volume=vol if vol > 0 else None,
                        )

        # Include volume gainers
        if vg_data and not isinstance(vg_data, Exception) and isinstance(vg_data, dict):
            for item in vg_data.get("data", []):
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol", "")).strip().upper()
                if not sym or not matches_index_filter(sym, index_filter):
                    continue
                ltp = float(item.get("ltp") or item.get("lastPrice") or 0.0)
                pct = float(item.get("pChange") or item.get("perChange") or 0.0)
                chg = float(item.get("change") or item.get("net_price") or 0.0)
                vol = int(item.get("totalTradedVolume") or item.get("quantityTraded") or 0)
                if pct > 0 and ltp > 0 and sym not in all_gainers:
                    name = self._lookup_company_name(sym)
                    all_gainers[sym] = MarketMover(
                        symbol=sym,
                        company_name=name,
                        exchange="NSE",
                        current_price=round(ltp, 2),
                        change=round(chg, 2),
                        change_percent=round(pct, 2),
                        volume=vol if vol > 0 else None,
                    )
                elif pct < 0 and ltp > 0 and sym not in all_losers:
                    name = self._lookup_company_name(sym)
                    all_losers[sym] = MarketMover(
                        symbol=sym,
                        company_name=name,
                        exchange="NSE",
                        current_price=round(ltp, 2),
                        change=round(chg, 2),
                        change_percent=round(pct, 2),
                        volume=vol if vol > 0 else None,
                    )

        # 2. Parse ALL variations groups for losers
        if l_data and not isinstance(l_data, Exception) and isinstance(l_data, dict):
            for group in variation_groups:
                group_data = l_data.get(group, {})
                items = group_data.get("data", []) if isinstance(group_data, dict) else (group_data if isinstance(group_data, list) else [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sym = str(item.get("symbol", "")).strip().upper()
                    if not sym or sym in all_losers or not matches_index_filter(sym, index_filter):
                        continue
                    ltp = float(item.get("ltp") or item.get("lastPrice") or 0.0)
                    prev = float(item.get("prev_price") or item.get("previousClose") or 0.0)
                    pct = float(item.get("perChange") or item.get("pChange") or 0.0)
                    chg = round(ltp - prev, 2) if prev else float(item.get("net_price") or item.get("change") or 0.0)
                    vol = int(item.get("trade_quantity") or item.get("totalTradedVolume") or item.get("quantityTraded") or 0)
                    if pct < 0 and ltp > 0:
                        name = self._lookup_company_name(sym)
                        all_losers[sym] = MarketMover(
                            symbol=sym,
                            company_name=name,
                            exchange="NSE",
                            current_price=round(ltp, 2),
                            change=round(chg, 2),
                            change_percent=round(pct, 2),
                            volume=vol if vol > 0 else None,
                        )

        # 3. Special completeness check for NIFTY 50:
        norm_filter = (index_filter or "all").lower().replace(" ", "").replace("_", "").replace("-", "")
        if norm_filter in ("nifty50", "50"):
            found_nifty = set(all_gainers.keys()) | set(all_losers.keys())
            missing_nifty = [s for s in NIFTY_50_SYMBOLS if s not in found_nifty]
            if missing_nifty:
                try:
                    for i in range(0, len(missing_nifty), 15):
                        chunk = missing_nifty[i : i + 15]
                        ns_tickers = [self._normalize_ticker(s) for s in chunk]
                        spark_url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={','.join(ns_tickers)}&range=1d&interval=1d"
                        s_res = await client.get(spark_url, timeout=4.0)
                        if s_res.status_code == 200:
                            s_data = s_res.json()
                            for t_sym, s_val in s_data.items():
                                if not s_val:
                                    continue
                                clean = t_sym.replace(".NS", "").replace(".BO", "")
                                ltp = float(s_val.get("fulldayPrice") or (s_val.get("close") and s_val["close"][-1]) or 0.0)
                                pct = float(s_val.get("fulldayChangePercent") or 0.0)
                                chg = float(s_val.get("fulldayChange") or 0.0)
                                if ltp > 0:
                                    name = self._lookup_company_name(clean)
                                    mover = MarketMover(
                                        symbol=clean,
                                        company_name=name,
                                        exchange="NSE",
                                        current_price=round(ltp, 2),
                                        change=round(chg, 2),
                                        change_percent=round(pct, 2),
                                        volume=None,
                                    )
                                    if pct >= 0:
                                        all_gainers[clean] = mover
                                    else:
                                        all_losers[clean] = mover
                except Exception as e:
                    logger.debug("Error fetching missing Nifty 50 constituents: %s", e)

        # 4. Parse Most Active (both volume and value feeds)
        for m_source in [m_data, mv_data]:
            if m_source and not isinstance(m_source, Exception) and isinstance(m_source, dict):
                items = m_source.get("data", []) if isinstance(m_source.get("data"), list) else []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sym = str(item.get("symbol", "")).strip().upper()
                    if not sym or sym in all_active or not matches_index_filter(sym, index_filter):
                        continue
                    ltp = float(item.get("lastPrice") or item.get("ltp") or 0.0)
                    chg = float(item.get("change") or item.get("net_price") or 0.0)
                    pct = float(item.get("pChange") or item.get("perChange") or 0.0)
                    vol = int(item.get("totalTradedVolume") or item.get("quantityTraded") or item.get("trade_quantity") or 0)
                    name = item.get("companyName") or self._lookup_company_name(sym)
                    if ltp > 0:
                        all_active[sym] = MarketMover(
                            symbol=sym,
                            company_name=name,
                            exchange="NSE",
                            current_price=round(ltp, 2),
                            change=round(chg, 2),
                            change_percent=round(pct, 2),
                            volume=vol if vol > 0 else None,
                        )

        # 5. Strictly sort all gainers descending and losers ascending
        gainers = list(all_gainers.values())
        gainers.sort(key=lambda x: x.change_percent, reverse=True)

        losers = list(all_losers.values())
        losers.sort(key=lambda x: x.change_percent)

        most_active = list(all_active.values())
        most_active.sort(key=lambda x: x.volume or 0, reverse=True)

        # Cache live movers snapshot in Redis/memory with 24hr TTL for reliable resilient offline fallback
        cache_filter_key = f"{self.SNAPSHOT_KEY_LIVE_MOVERS}:{norm_filter}"
        if gainers or losers or most_active:
            snapshot_payload = {
                "gainers": [g.model_dump() for g in gainers[:limit]],
                "losers": [l.model_dump() for l in losers[:limit]],
                "most_active": [m.model_dump() for m in most_active[:limit]],
            }
            try:
                await market_cache.set(cache_filter_key, snapshot_payload, ttl_seconds=86400)
            except Exception:
                pass

        return gainers[:limit], losers[:limit], most_active[:limit]

    async def get_market_overview(
        self, index_filter: Optional[str] = None, limit: int = 20
    ) -> MarketOverview:
        indices_task = self.get_indices()
        movers_task = self._fetch_live_nse_movers(index_filter=index_filter, limit=limit)

        indices, (gainers, losers, most_active) = await asyncio.gather(
            indices_task, movers_task
        )

        norm_filter = (index_filter or "all").lower().replace(" ", "").replace("_", "").replace("-", "")
        cache_filter_key = f"{self.SNAPSHOT_KEY_LIVE_MOVERS}:{norm_filter}"

        # Fallback to cached snapshot if live NSE fetch returned empty
        if not gainers or not losers:
            try:
                snap = await market_cache.get(cache_filter_key)
                if not snap:
                    snap = await market_cache.get(f"{self.SNAPSHOT_KEY_LIVE_MOVERS}:all")
                if snap and isinstance(snap, dict):
                    if not gainers and snap.get("gainers"):
                        gainers = [MarketMover(**item) for item in snap["gainers"]]
                    if not losers and snap.get("losers"):
                        losers = [MarketMover(**item) for item in snap["losers"]]
                    if not most_active and snap.get("most_active"):
                        most_active = [MarketMover(**item) for item in snap["most_active"]]
            except Exception:
                pass

        # Final fallback: sample popular liquid stocks or constituents if still empty
        if not gainers or not losers:
            if norm_filter in ("nifty50", "50"):
                sample_symbols = list(NIFTY_50_SYMBOLS)[:30]
            elif norm_filter in ("nifty100", "100"):
                sample_symbols = list(NIFTY_100_SYMBOLS)[:30]
            elif norm_filter in ("midcap100", "midcap"):
                sample_symbols = list(NIFTY_MIDCAP_100_SYMBOLS)[:30]
            elif norm_filter in ("smallcap100", "smallcap"):
                sample_symbols = list(NIFTY_SMALLCAP_100_SYMBOLS)[:30]
            elif norm_filter in ("nifty500", "500"):
                sample_symbols = list(NIFTY_500_SYMBOLS)[:30]
            else:
                sample_symbols = [item["clean"] for item in self.POPULAR_INDIAN_STOCKS[:30]]

            quotes = await self.get_quotes(sample_symbols)
            valid_quotes = [q for q in quotes if q.current_price > 0]
            sorted_by_change = sorted(valid_quotes, key=lambda x: x.change_percent, reverse=True)
            if not gainers:
                gainers = [
                    MarketMover(
                        symbol=q.symbol,
                        company_name=q.company_name,
                        exchange=q.exchange,
                        current_price=q.current_price,
                        change=q.change,
                        change_percent=q.change_percent,
                        volume=q.volume,
                    )
                    for q in sorted_by_change[:limit]
                    if q.change_percent > 0
                ]
            if not losers:
                losers = [
                    MarketMover(
                        symbol=q.symbol,
                        company_name=q.company_name,
                        exchange=q.exchange,
                        current_price=q.current_price,
                        change=q.change,
                        change_percent=q.change_percent,
                        volume=q.volume,
                    )
                    for q in sorted_by_change[-limit:]
                    if q.change_percent < 0
                ]
                losers.reverse()
            if not most_active:
                sorted_by_volume = sorted(valid_quotes, key=lambda x: x.volume or 0, reverse=True)
                most_active = [
                    MarketMover(
                        symbol=q.symbol,
                        company_name=q.company_name,
                        exchange=q.exchange,
                        current_price=q.current_price,
                        change=q.change,
                        change_percent=q.change_percent,
                        volume=q.volume,
                    )
                    for q in sorted_by_volume[:limit]
                ]

        is_open = self._is_indian_market_open()
        status_text = "OPEN" if is_open else "CLOSED"
        msg = (
            "NSE/BSE live market active"
            if is_open
            else "Market closed (Normal trading hours: 09:15 - 15:30 IST Mon-Fri)"
        )

        return MarketOverview(
            market_status=status_text,
            status_message=msg,
            major_indices=indices,
            top_gainers=gainers[:limit],
            top_losers=losers[:limit],
            most_active=most_active[:limit],
        )


    async def get_historical_candles(
        self, symbol: str, interval: str = "1d", range_period: str = "1mo"
    ) -> Optional[StockHistoryResponse]:
        ticker_symbol = self._normalize_ticker(symbol)
        encoded_ticker = urllib.parse.quote(ticker_symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?interval={interval}&range={range_period}"

        try:
            client = await self._get_client()
            async with self._get_semaphore():
                res = await client.get(url)
                if res.status_code != 200 or not res.json().get("chart", {}).get("result"):
                    if ticker_symbol.endswith(".NS"):
                        bse_ticker = ticker_symbol.replace(".NS", ".BO")
                        encoded_bse = urllib.parse.quote(bse_ticker)
                        bse_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_bse}?interval={interval}&range={range_period}"
                        res = await client.get(bse_url)

                if res.status_code == 200:
                    data = res.json()
                    results = data.get("chart", {}).get("result", [])
                    if not results:
                        return None

                    item = results[0]
                    timestamps = item.get("timestamp", [])
                    indicators = item.get("indicators", {}).get("quote", [{}])[0]

                    opens = indicators.get("open", [])
                    highs = indicators.get("high", [])
                    lows = indicators.get("low", [])
                    closes = indicators.get("close", [])
                    volumes = indicators.get("volume", [])

                    candles = []
                    for i, ts in enumerate(timestamps):
                        if (
                            i < len(opens)
                            and opens[i] is not None
                            and highs[i] is not None
                            and lows[i] is not None
                            and closes[i] is not None
                        ):
                            dt = datetime.datetime.fromtimestamp(ts, tz=pytz.timezone("Asia/Kolkata"))
                            candles.append(
                                CandleData(
                                    timestamp=dt,
                                    open=round(float(opens[i]), 2),
                                    high=round(float(highs[i]), 2),
                                    low=round(float(lows[i]), 2),
                                    close=round(float(closes[i]), 2),
                                    volume=int(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0,
                                )
                            )

                    clean_sym = ticker_symbol.replace(".NS", "").replace(".BO", "")
                    return StockHistoryResponse(
                        symbol=clean_sym,
                        interval=interval,
                        range=range_period,
                        candles=candles,
                    )
        except Exception as exc:
            logger.debug("Error fetching historical candles for '%s': %s", symbol, exc)
            return None

        return None

    async def search_symbols(self, query: str) -> List[StockSearchResult]:
        q = query.strip()
        results: List[StockSearchResult] = []
        seen_symbols = set()

        # 1. First check our curated list of 50+ major Indian stocks
        q_clean = q.upper().replace(".NS", "").replace(".BO", "")
        q_norm = q.lower().replace("&", "").replace("-", "").replace(" ", "")

        for stock in self.POPULAR_INDIAN_STOCKS:
            stock_clean = stock["clean"].upper()
            stock_clean_norm = stock_clean.lower().replace("&", "").replace("-", "").replace(" ", "")

            is_ticker_match = (
                q_clean == stock_clean
                or q_norm == stock_clean_norm
                or stock_clean.startswith(q_clean)
            )

            name_words = [
                w.strip(".,()").lower()
                for w in stock["name"].split()
                if w.strip(".,()").lower() not in ["ltd", "limited", "corp", "corporation", "industries", "co", "the", "company", "ind"]
            ]
            is_name_match = any(q_norm in w or w.startswith(q_norm) for w in name_words if len(w) >= 2)

            if is_ticker_match or is_name_match:
                if stock["clean"] not in seen_symbols:
                    seen_symbols.add(stock["clean"])
                    results.append(
                        StockSearchResult(
                            symbol=stock["clean"],
                            name=stock["name"],
                            exchange="NSE",
                            instrument_type="EQUITY",
                        )
                    )

        # 2. Query live Yahoo Finance Indian search API
        try:
            search_queries = [q]
            if "&" in q:
                search_queries.append(q.replace("&", " and "))
                search_queries.append(q.replace("&", ""))

            client = await self._get_client()
            for sq in search_queries:
                encoded_query = urllib.parse.quote(sq)
                search_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={encoded_query}&quotesCount=15&newsCount=0"
                res = await client.get(search_url)
                if res.status_code == 200:
                    quotes = res.json().get("quotes", [])
                    for item in quotes:
                        sym = item.get("symbol", "")
                        exch = item.get("exchange", "")
                        if sym.startswith("0P00"):
                            continue
                        if exch in ["NSI", "NSE", "BSE", "BOM"] or sym.endswith((".NS", ".BO")):
                            clean_sym = sym.replace(".NS", "").replace(".BO", "")
                            if clean_sym not in seen_symbols:
                                seen_symbols.add(clean_sym)
                                exchange_name = "BSE" if sym.endswith(".BO") or exch in ["BSE", "BOM"] else "NSE"
                                name = item.get("shortname") or item.get("longname") or clean_sym
                                results.append(
                                    StockSearchResult(
                                        symbol=clean_sym,
                                        name=name,
                                        exchange=exchange_name,
                                        instrument_type=item.get("quoteType", "EQUITY"),
                                    )
                                )
        except Exception:
            pass

        # 3. Direct quote fallback if still empty
        if not results and len(q) >= 2:
            try:
                direct_quote = await self.get_quote(q)
                if direct_quote and direct_quote.current_price > 0:
                    if direct_quote.symbol not in seen_symbols:
                        seen_symbols.add(direct_quote.symbol)
                        results.append(
                            StockSearchResult(
                                symbol=direct_quote.symbol,
                                name=direct_quote.company_name,
                                exchange=direct_quote.exchange,
                                instrument_type="EQUITY",
                            )
                        )
            except Exception:
                pass

        return results
