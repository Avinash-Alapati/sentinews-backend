"""
NSE Official Market Data Adapter.

Retrieves domestic Indian indices, sector performance, top gainers/losers,
and daily FII/DII institutional disclosures directly from NSE endpoints with
automatic session management, cookie renewal, and dynamic real-time fallback.

LEGAL & COMPLIANCE NOTICE:
Data returned by this adapter is scraped from public disclosures on the NSE website.
The FII/DII disclosure endpoint (/api/fiidiiTradeReact) and market indices are provided
for informational analysis only. Commercial redistribution or white-labeling must be
reviewed against NSE India data licensing and terms of service before production syndication.

Architecture:
- HTTP session cookie handshake with automatic session renewal on 401/403.
- Outbound pacing via OutboundRateLimiter.
- Multi-tier fallback: Live NSE APIs -> Real-Time Broad-Basket Multi-Quote -> Snapshot Cache.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import (
    FIIDIIData,
    IndexPerformanceItem,
    SectorPerformanceItem,
    TopMoverItem,
)
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import outbound_limiter

logger = logging.getLogger("sentinews.market_reports.nse_adapter")

NSE_BASE_URL = "https://www.nseindia.com"
NSE_ALL_INDICES_URL = "https://www.nseindia.com/api/allIndices"
NSE_GAINERS_URL = "https://www.nseindia.com/api/live-analysis-variations?index=gainers"
NSE_LOSERS_URL = "https://www.nseindia.com/api/live-analysis-variations?index=loosers"
NSE_LOSERS_FALLBACK_URL = "https://www.nseindia.com/api/live-analysis-variations?index=losers"
NSE_FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_ETF_URL = "https://www.nseindia.com/api/etf"

CACHE_KEY_NSE_INDICES = "market_reports:adapter:nse_indices"
CACHE_KEY_NSE_MOVERS = "market_reports:adapter:nse_movers"
CACHE_KEY_NSE_FIIDII = "market_reports:adapter:nse_fiidii"
CACHE_KEY_NSE_ETFS = "market_reports:adapter:nse_etfs"
SNAPSHOT_KEY_NSE_INDICES = "market_reports:snapshot:nse_indices"
SNAPSHOT_KEY_NSE_MOVERS = "market_reports:snapshot:nse_movers"
SNAPSHOT_KEY_NSE_FIIDII = "market_reports:snapshot:nse_fiidii"
SNAPSHOT_KEY_NSE_ETFS = "market_reports:snapshot:nse_etfs"

CACHE_TTL_INDICES = 300  # 5 minutes
CACHE_TTL_FIIDII = 1800  # 30 minutes
CACHE_TTL_ETFS = 300  # 5 minutes

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# Major broad and benchmark Indian indices mapping
BROAD_INDEX_SYMBOLS = {
    "NIFTY 50": "Nifty 50",
    "NIFTY NEXT 50": "Nifty Next 50",
    "NIFTY 100": "Nifty 100",
    "NIFTY 200": "Nifty 200",
    "NIFTY 500": "Nifty 500",
    "NIFTY MIDCAP 50": "Nifty Midcap 50",
    "NIFTY MIDCAP 100": "Nifty Midcap 100",
    "NIFTY SMALLCAP 100": "Nifty Smallcap 100",
}

# Sectoral index keywords on NSE
SECTOR_INDEX_KEYWORDS = [
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY AUTO",
    "NIFTY PHARMA",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY ENERGY",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY HEALTHCARE",
    "NIFTY MEDIA",
    "NIFTY OIL & GAS",
    "NIFTY CONSUMER DURABLES",
]

# Fallback Yahoo tickers for Indian indices
INDEX_FALLBACK_MAP = [
    {"symbol": "^NSEI", "name": "Nifty 50", "category": "broad", "display": "NIFTY 50"},
    {"symbol": "^BSESN", "name": "Sensex", "category": "broad", "display": "BSE SENSEX"},
    {"symbol": "^NSEBANK", "name": "Nifty Bank", "category": "sector", "display": "NIFTY BANK"},
    {"symbol": "^CNXIT", "name": "Nifty IT", "category": "sector", "display": "NIFTY IT"},
    {"symbol": "^CNXAUTO", "name": "Nifty Auto", "category": "sector", "display": "NIFTY AUTO"},
    {"symbol": "^CNXPHARMA", "name": "Nifty Pharma", "category": "sector", "display": "NIFTY PHARMA"},
    {"symbol": "^CNXFMCG", "name": "Nifty FMCG", "category": "sector", "display": "NIFTY FMCG"},
    {"symbol": "^CNXMETAL", "name": "Nifty Metal", "category": "sector", "display": "NIFTY METAL"},
    {"symbol": "^CNXREALTY", "name": "Nifty Realty", "category": "sector", "display": "NIFTY REALTY"},
    {"symbol": "^CNXENERGY", "name": "Nifty Energy", "category": "sector", "display": "NIFTY ENERGY"},
    {"symbol": "NIFTY_MIDCAP_100.NS", "name": "Nifty Midcap 100", "category": "broad", "display": "NIFTY MIDCAP 100"},
]

# Complete 50-stock Nifty basket for real-time fallback calculation
NIFTY_50_CONSTITUENTS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BPCL", "BHARTIARTL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC",
    "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NTPC", "NESTLEIND", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

CONSTITUENT_NAMES = {
    "ADANIENT": "Adani Enterprises Ltd",
    "ADANIPORTS": "Adani Ports & SEZ Ltd",
    "APOLLOHOSP": "Apollo Hospitals Enterprise Ltd",
    "ASIANPAINT": "Asian Paints Ltd",
    "AXISBANK": "Axis Bank Ltd",
    "BAJAJ-AUTO": "Bajaj Auto Ltd",
    "BAJFINANCE": "Bajaj Finance Ltd",
    "BAJAJFINSV": "Bajaj Finserv Ltd",
    "BPCL": "Bharat Petroleum Corporation Ltd",
    "BHARTIARTL": "Bharti Airtel Ltd",
    "BRITANNIA": "Britannia Industries Ltd",
    "CIPLA": "Cipla Ltd",
    "COALINDIA": "Coal India Ltd",
    "DIVISLAB": "Divi's Laboratories Ltd",
    "DRREDDY": "Dr. Reddy's Laboratories Ltd",
    "EICHERMOT": "Eicher Motors Ltd",
    "GRASIM": "Grasim Industries Ltd",
    "HCLTECH": "HCL Technologies Ltd",
    "HDFCBANK": "HDFC Bank Ltd",
    "HDFCLIFE": "HDFC Life Insurance Co Ltd",
    "HEROMOTOCO": "Hero MotoCorp Ltd",
    "HINDALCO": "Hindalco Industries Ltd",
    "HINDUNILVR": "Hindustan Unilever Ltd",
    "ICICIBANK": "ICICI Bank Ltd",
    "ITC": "ITC Ltd",
    "INDUSINDBK": "IndusInd Bank Ltd",
    "INFY": "Infosys Ltd",
    "JSWSTEEL": "JSW Steel Ltd",
    "KOTAKBANK": "Kotak Mahindra Bank Ltd",
    "LT": "Larsen & Toubro Ltd",
    "M&M": "Mahindra & Mahindra Ltd",
    "MARUTI": "Maruti Suzuki India Ltd",
    "NTPC": "NTPC Ltd",
    "NESTLEIND": "Nestle India Ltd",
    "ONGC": "Oil & Natural Gas Corporation Ltd",
    "POWERGRID": "Power Grid Corporation of India Ltd",
    "RELIANCE": "Reliance Industries Ltd",
    "SBILIFE": "SBI Life Insurance Co Ltd",
    "SHRIRAMFIN": "Shriram Finance Ltd",
    "SBIN": "State Bank of India",
    "SUNPHARMA": "Sun Pharmaceutical Industries Ltd",
    "TCS": "Tata Consultancy Services Ltd",
    "TATACONSUM": "Tata Consumer Products Ltd",
    "TATAMOTORS": "Tata Motors Ltd",
    "TATASTEEL": "Tata Steel Ltd",
    "TECHM": "Tech Mahindra Ltd",
    "TITAN": "Titan Company Ltd",
    "TRENT": "Trent Ltd",
    "ULTRACEMCO": "UltraTech Cement Ltd",
    "WIPRO": "Wipro Ltd",
}


def _safe_float(val: Any) -> Optional[float]:
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    if val is None or val == "" or val == "-":
        return None
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return None


def categorize_etf(asset: str, symbol: str) -> str:
    """Categorizes Indian ETFs into standard investment groups."""
    combined = f"{asset} {symbol}".lower()
    if "gold" in combined:
        return "Commodity - Gold"
    if "silver" in combined:
        return "Commodity - Silver"
    if any(k in combined for k in ["liquid", "g-sec", "gsec", "bond", "gilt", "debt", "1d rate", "overnight", "government"]):
        return "Debt & Liquid"
    if any(k in combined for k in ["nasdaq", "fang", "s&p 500", "hang seng"]):
        return "Global / International"
    if any(k in combined for k in ["momentum", "alpha", "quality", "value", "low vol", "equal weight", "multicap", "esg", "dividend"]):
        return "Equity - Factor & Smart Beta"
    if any(k in combined for k in ["bank", "it", "pharma", "auto", "fmcg", "metal", "realty", "energy", "infra", "defence", "oil", "healthcare", "railway", "chemical", "cement", "tourism", "consumption", "power", "insurance", "hospital", "ev", "pse", "psu"]):
        return "Equity - Sectoral & Thematic"
    if any(k in combined for k in ["nifty 50", "sensex", "nifty 100", "nifty 500", "midcap", "smallcap", "next 50", "200", "250", "nifty"]):
        return "Equity - Broad Market"
    return "Other"


class NSEMarketDataProvider:
    """
    Adapter interfacing with NSE official endpoints with cookie management and resilience.
    """

    def __init__(self, timeout: float = 10.0, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
        self._handshake_done = False
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=NSE_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
                http2=False,
            )
            self._handshake_done = False
        return self._client

    async def _ensure_handshake(self, force_refresh: bool = False) -> None:
        """Establishes session cookies by accessing the NSE homepage."""
        async with self._lock:
            if self._handshake_done and not force_refresh:
                return

            client = await self._get_client()
            try:
                async with outbound_limiter.pace("nse"):
                    resp = await client.get(NSE_BASE_URL)
                    if resp.status_code in (200, 302, 304, 403):
                        self._handshake_done = True
                        logger.debug("NSE session handshake initialized (cookies=%d).", len(client.cookies))
            except Exception as exc:
                logger.warning("NSE session handshake attempt failed: %s", exc)

    async def _fetch_json(self, url: str) -> Optional[Any]:
        """Fetches JSON endpoint with retries, pacing, and cookie renewal."""
        client = await self._get_client()

        for attempt in range(1, self.max_retries + 1):
            try:
                await self._ensure_handshake(force_refresh=(attempt > 1))
                async with outbound_limiter.pace("nse"):
                    resp = await client.get(url, headers=NSE_HEADERS)

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code in (401, 403):
                    logger.warning("NSE endpoint returned status %d on attempt %d. Refreshing session.", resp.status_code, attempt)
                    self._handshake_done = False
                    await asyncio.sleep(0.5 * attempt)
                else:
                    logger.warning("NSE endpoint %s returned status %d", url, resp.status_code)
            except Exception as exc:
                logger.warning("NSE endpoint %s error on attempt %d: %s", url, attempt, exc)
                await asyncio.sleep(0.5 * attempt)

        return None

    # =========================================================================
    # Public Domain Query Methods
    # =========================================================================

    async def get_indian_indices(self) -> Tuple[List[IndexPerformanceItem], List[SectorPerformanceItem]]:
        """
        Fetches both broad Indian benchmark indices and sectoral index performance.
        Returns (broad_indices, sector_indices).
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_NSE_INDICES)
        if cached and isinstance(cached, dict):
            broad = [IndexPerformanceItem(**item) for item in cached.get("broad", [])]
            sectors = [SectorPerformanceItem(**item) for item in cached.get("sectors", [])]
            return broad, sectors

        # 2. Query NSE /api/allIndices
        broad_results: List[IndexPerformanceItem] = []
        sector_results: List[SectorPerformanceItem] = []

        try:
            data = await self._fetch_json(NSE_ALL_INDICES_URL)
            if data and isinstance(data, dict) and "data" in data:
                items = data.get("data", [])
                for it in items:
                    idx_name = str(it.get("index", "")).strip()
                    last_price = float(it.get("last", 0.0) or 0.0)
                    pct_change = float(it.get("percentChange", 0.0) or 0.0)
                    prev_close = float(it.get("previousClose", 0.0) or 0.0)
                    change = float(it.get("variation", 0.0) or (last_price - prev_close if prev_close > 0 else 0.0))
                    advances = int(it.get("advances", 0) or 0)
                    declines = int(it.get("declines", 0) or 0)

                    # Check broad index
                    if idx_name in BROAD_INDEX_SYMBOLS:
                        broad_results.append(
                            IndexPerformanceItem(
                                symbol=idx_name,
                                name=BROAD_INDEX_SYMBOLS[idx_name],
                                current_price=round(last_price, 2),
                                change=round(change, 2),
                                change_percent=round(pct_change, 2),
                            )
                        )

                    # Check sectoral index
                    if idx_name in SECTOR_INDEX_KEYWORDS or idx_name.startswith("NIFTY ") and any(k in idx_name for k in ["BANK", "IT", "AUTO", "PHARMA", "FMCG", "METAL", "REALTY", "ENERGY", "FINANCIAL", "HEALTHCARE", "MEDIA", "OIL"]):
                        sector_results.append(
                            SectorPerformanceItem(
                                sector=idx_name,
                                change_percent=round(pct_change, 2),
                                advances=advances,
                                declines=declines,
                            )
                        )

                if broad_results:
                    payload = {
                        "broad": [b.__dict__ for b in broad_results],
                        "sectors": [s.__dict__ for s in sector_results],
                    }
                    await market_cache.set(CACHE_KEY_NSE_INDICES, payload, ttl_seconds=CACHE_TTL_INDICES)
                    await market_cache.set(SNAPSHOT_KEY_NSE_INDICES, payload, ttl_seconds=86400)
                    return broad_results, sector_results
        except Exception as exc:
            logger.warning("Failed parsing NSE allIndices: %s", exc)

        # 3. Live Yahoo Chart Fallback for Indian Indices
        try:
            fb_broad, fb_sectors = await self._fetch_indices_yahoo_fallback()
            if fb_broad:
                payload = {
                    "broad": [b.__dict__ for b in fb_broad],
                    "sectors": [s.__dict__ for s in fb_sectors],
                }
                await market_cache.set(CACHE_KEY_NSE_INDICES, payload, ttl_seconds=CACHE_TTL_INDICES)
                await market_cache.set(SNAPSHOT_KEY_NSE_INDICES, payload, ttl_seconds=86400)
                return fb_broad, fb_sectors
        except Exception as exc:
            logger.warning("Yahoo fallback for Indian indices failed: %s", exc)

        # 4. Snapshot Fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_NSE_INDICES)
        if snapshot and isinstance(snapshot, dict):
            logger.info("Serving NSE indices from last successful snapshot.")
            broad = [IndexPerformanceItem(**item) for item in snapshot.get("broad", [])]
            sectors = [SectorPerformanceItem(**item) for item in snapshot.get("sectors", [])]
            return broad, sectors

        return [], []

    async def _fetch_indices_yahoo_fallback(self) -> Tuple[List[IndexPerformanceItem], List[SectorPerformanceItem]]:
        """Fast asynchronous chart API fallback for Indian Indices."""
        broad: List[IndexPerformanceItem] = []
        sectors: List[SectorPerformanceItem] = []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=5.0) as client:
            async def _fetch(item: Dict[str, str]):
                sym = item["symbol"]
                for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
                    url = f"https://{host}/v8/finance/chart/{sym}?interval=1d&range=5d"
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            data = resp.json()
                            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                            price = float(meta.get("regularMarketPrice") or 0.0)
                            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0)
                            if price > 0:
                                change = round(price - prev if prev > 0 else 0.0, 2)
                                pct = round((change / prev * 100.0) if prev > 0 else 0.0, 2)
                                return item, price, change, pct
                    except Exception:
                        continue
                return item, 0.0, 0.0, 0.0

            results = await asyncio.gather(*[_fetch(it) for it in INDEX_FALLBACK_MAP])
            for it, price, chg, pct in results:
                if price <= 0:
                    continue
                if it["category"] == "broad":
                    broad.append(
                        IndexPerformanceItem(
                            symbol=it["display"],
                            name=it["name"],
                            current_price=price,
                            change=chg,
                            change_percent=pct,
                        )
                    )
                else:
                    sectors.append(
                        SectorPerformanceItem(
                            sector=it["display"],
                            change_percent=pct,
                            advances=None,
                            declines=None,
                        )
                    )

        return broad, sectors

    async def get_top_gainers_and_losers(self) -> Tuple[List[TopMoverItem], List[TopMoverItem]]:
        """
        Retrieves today's top gainers and losers from NSE live analysis variations
        or dynamic 50-stock real-time constituent evaluation.
        Returns (top_gainers, top_losers).
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_NSE_MOVERS)
        if cached and isinstance(cached, dict):
            gainers = [TopMoverItem(**item) for item in cached.get("gainers", [])]
            losers = [TopMoverItem(**item) for item in cached.get("losers", [])]
            if gainers and losers:
                return gainers, losers

        gainers: List[TopMoverItem] = []
        losers: List[TopMoverItem] = []

        try:
            from app.integrations.market.indian_market_provider import IndianMarketProvider
            name_map = getattr(IndianMarketProvider, "STOCK_NAME_MAP", {})
        except Exception:
            name_map = {}

        def get_company_name(sym: str) -> str:
            clean = sym.strip().upper().replace(".NS", "").replace(".BO", "")
            return name_map.get(clean, clean)

        try:
            # Fetch Gainers
            gainers_raw = await self._fetch_json(NSE_GAINERS_URL)
            if gainers_raw and isinstance(gainers_raw, dict):
                seen_g = set()
                for group in ["NIFTY", "FOSec", "allSec"]:
                    group_data = gainers_raw.get(group, {})
                    items = group_data.get("data", []) if isinstance(group_data, dict) else (group_data if isinstance(group_data, list) else [])
                    for g in items:
                        if not isinstance(g, dict):
                            continue
                        sym = str(g.get("symbol", "")).strip().upper()
                        if sym and sym not in seen_g:
                            seen_g.add(sym)
                            ltp = float(g.get("ltp", 0.0) or g.get("lastPrice", 0.0) or 0.0)
                            pct = float(g.get("perChange", 0.0) or g.get("pChange", 0.0) or 0.0)
                            if pct > 0 and ltp > 0:
                                gainers.append(
                                    TopMoverItem(
                                        symbol=sym,
                                        company_name=get_company_name(sym),
                                        current_price=round(ltp, 2),
                                        change_percent=round(pct, 2),
                                        direction="gainer",
                                    )
                                )
                gainers.sort(key=lambda x: x.change_percent, reverse=True)

            # Fetch Losers (Try index=loosers first, then index=losers)
            losers_raw = await self._fetch_json(NSE_LOSERS_URL)
            if not losers_raw or (isinstance(losers_raw, dict) and "Missing" in str(losers_raw.get("data", ""))):
                losers_raw = await self._fetch_json(NSE_LOSERS_FALLBACK_URL)

            if losers_raw and isinstance(losers_raw, dict):
                seen_l = set()
                for group in ["NIFTY", "FOSec", "allSec"]:
                    group_data = losers_raw.get(group, {})
                    items = group_data.get("data", []) if isinstance(group_data, dict) else (group_data if isinstance(group_data, list) else [])
                    for l in items:
                        if not isinstance(l, dict):
                            continue
                        sym = str(l.get("symbol", "")).strip().upper()
                        if sym and sym not in seen_l:
                            seen_l.add(sym)
                            ltp = float(l.get("ltp", 0.0) or l.get("lastPrice", 0.0) or 0.0)
                            pct = float(l.get("perChange", 0.0) or l.get("pChange", 0.0) or 0.0)
                            if pct < 0 and ltp > 0:
                                losers.append(
                                    TopMoverItem(
                                        symbol=sym,
                                        company_name=get_company_name(sym),
                                        current_price=round(ltp, 2),
                                        change_percent=round(pct, 2),
                                        direction="loser",
                                    )
                                )
                losers.sort(key=lambda x: x.change_percent)

            # If losers or gainers is empty, engage dynamic basket fallback
            if not losers or not gainers:
                fb_gainers, fb_losers = await self._fetch_movers_basket_fallback()
                if not gainers and fb_gainers:
                    gainers = fb_gainers
                if not losers and fb_losers:
                    losers = fb_losers

            if not losers:
                snapshot = await market_cache.get(SNAPSHOT_KEY_NSE_MOVERS)
                if snapshot and isinstance(snapshot, dict) and "losers" in snapshot and len(snapshot["losers"]) > 0:
                    losers = [TopMoverItem(**item) for item in snapshot["losers"]]

            if gainers and losers:
                payload = {
                    "gainers": [g.__dict__ for g in gainers[:10]],
                    "losers": [l.__dict__ for l in losers[:10]],
                }
                await market_cache.set(CACHE_KEY_NSE_MOVERS, payload, ttl_seconds=CACHE_TTL_INDICES)
                await market_cache.set(SNAPSHOT_KEY_NSE_MOVERS, payload, ttl_seconds=86400)
                return gainers[:10], losers[:10]
        except Exception as exc:
            logger.warning("Failed fetching NSE gainers/losers: %s", exc)

        # Dynamic 50-stock real-time fallback
        try:
            fb_gainers, fb_losers = await self._fetch_movers_basket_fallback()
            if fb_gainers or fb_losers:
                payload = {
                    "gainers": [g.__dict__ for g in fb_gainers],
                    "losers": [l.__dict__ for l in fb_losers],
                }
                await market_cache.set(CACHE_KEY_NSE_MOVERS, payload, ttl_seconds=CACHE_TTL_INDICES)
                await market_cache.set(SNAPSHOT_KEY_NSE_MOVERS, payload, ttl_seconds=86400)
                return fb_gainers, fb_losers
        except Exception as exc:
            logger.warning("Dynamic constituent basket fallback for movers failed: %s", exc)

        # Snapshot fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_NSE_MOVERS)
        if snapshot and isinstance(snapshot, dict):
            gainers = [TopMoverItem(**item) for item in snapshot.get("gainers", [])]
            losers = [TopMoverItem(**item) for item in snapshot.get("losers", [])]
            return gainers[:10], losers[:10]

        return [], []

    async def _fetch_movers_basket_fallback(self) -> Tuple[List[TopMoverItem], List[TopMoverItem]]:
        """Evaluates live quotes across all 50 Nifty constituents concurrently."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

        quotes: List[Tuple[str, str, float, float]] = []  # (symbol, name, price, pct)

        async with httpx.AsyncClient(headers=headers, timeout=5.0) as client:
            sem = asyncio.Semaphore(20)

            async def _fetch_quote(sym: str):
                async with sem:
                    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
                        url = f"https://{host}/v8/finance/chart/{sym}.NS?interval=1d&range=5d"
                        try:
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                data = resp.json()
                                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                                price = float(meta.get("regularMarketPrice") or 0.0)
                                prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0)
                                if price > 0 and prev > 0:
                                    pct = round(((price - prev) / prev) * 100.0, 2)
                                    name = CONSTITUENT_NAMES.get(sym, sym)
                                    return sym, name, price, pct
                        except Exception:
                            continue
                    return sym, CONSTITUENT_NAMES.get(sym, sym), 0.0, 0.0

            results = await asyncio.gather(*[_fetch_quote(sym) for sym in NIFTY_50_CONSTITUENTS])
            for sym, name, price, pct in results:
                if price > 0:
                    quotes.append((sym, name, price, pct))

        if not quotes:
            return [], []

        sorted_gainers = sorted(quotes, key=lambda x: x[3], reverse=True)
        sorted_losers = sorted(quotes, key=lambda x: x[3])

        gainers = [
            TopMoverItem(
                symbol=q[0],
                company_name=q[1],
                current_price=q[2],
                change_percent=q[3],
                direction="gainer",
            )
            for q in sorted_gainers[:10] if q[3] >= 0
        ]

        losers = [
            TopMoverItem(
                symbol=q[0],
                company_name=q[1],
                current_price=q[2],
                change_percent=q[3],
                direction="loser",
            )
            for q in sorted_losers[:10] if q[3] <= 0
        ]

        return gainers, losers

    async def get_fii_dii_data(self) -> Optional[FIIDIIData]:
        """
        Retrieves daily FII/DII institutional trading activity disclosure in INR Crores.
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_NSE_FIIDII)
        if cached and isinstance(cached, dict):
            return FIIDIIData(**cached)

        try:
            data = await self._fetch_json(NSE_FIIDII_URL)
            if data and isinstance(data, list) and len(data) >= 2:
                dii_entry = next((x for x in data if "DII" in x.get("category", "").upper()), None)
                fii_entry = next((x for x in data if "FII" in x.get("category", "").upper() or "FPI" in x.get("category", "").upper()), None)

                if dii_entry and fii_entry:
                    report_date = dii_entry.get("date") or fii_entry.get("date") or datetime.now(timezone.utc).strftime("%d-%b-%Y")
                    fii_buy = float(str(fii_entry.get("buyValue", "0")).replace(",", ""))
                    fii_sell = float(str(fii_entry.get("sellValue", "0")).replace(",", ""))
                    fii_net = float(str(fii_entry.get("netValue", "0")).replace(",", ""))

                    dii_buy = float(str(dii_entry.get("buyValue", "0")).replace(",", ""))
                    dii_sell = float(str(dii_entry.get("sellValue", "0")).replace(",", ""))
                    dii_net = float(str(dii_entry.get("netValue", "0")).replace(",", ""))

                    fii_dii = FIIDIIData(
                        date=report_date,
                        fii_buy=round(fii_buy, 2),
                        fii_sell=round(fii_sell, 2),
                        fii_net=round(fii_net, 2),
                        dii_buy=round(dii_buy, 2),
                        dii_sell=round(dii_sell, 2),
                        dii_net=round(dii_net, 2),
                        unit="INR_CRORES",
                        source_note="NSE Institutional Activity Disclosure (Informational Use)",
                    )

                    await market_cache.set(CACHE_KEY_NSE_FIIDII, fii_dii.__dict__, ttl_seconds=CACHE_TTL_FIIDII)
                    await market_cache.set(SNAPSHOT_KEY_NSE_FIIDII, fii_dii.__dict__, ttl_seconds=86400)
                    return fii_dii
        except Exception as exc:
            logger.warning("Failed fetching NSE FII/DII data: %s", exc)

        # Snapshot fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_NSE_FIIDII)
        if snapshot and isinstance(snapshot, dict):
            logger.info("Serving FII/DII data from last known snapshot.")
            return FIIDIIData(**snapshot)

        return None

    async def get_etfs(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves real-time ETF data from official NSE endpoint.
        Returns a list of parsed ETF records with caching and snapshot resilience.
        """
        raw_etfs: Optional[List[Dict[str, Any]]] = None

        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_NSE_ETFS)
        if cached and isinstance(cached, list):
            raw_etfs = cached
        else:
            try:
                data = await self._fetch_json(NSE_ETF_URL)
                if data and isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    items = []
                    for entry in data["data"]:
                        sym = str(entry.get("symbol", "")).strip()
                        asset = str(entry.get("assets", "")).strip()
                        ltp = _safe_float(entry.get("ltP")) or 0.0
                        chn = _safe_float(entry.get("chn")) or 0.0
                        per = _safe_float(entry.get("per")) or 0.0

                        cat = categorize_etf(asset, sym)

                        parsed = {
                            "symbol": sym,
                            "underlying_asset": asset,
                            "category": cat,
                            "last_price": round(ltp, 2),
                            "change": round(chn, 2),
                            "change_percent": round(per, 2),
                            "open": _safe_float(entry.get("open")),
                            "high": _safe_float(entry.get("high")),
                            "low": _safe_float(entry.get("low")),
                            "previous_close": _safe_float(entry.get("prevClose")),
                            "volume": _safe_int(entry.get("qty")),
                            "traded_value": _safe_float(entry.get("trdVal")),
                            "nav": _safe_float(entry.get("nav")),
                            "fifty_two_week_high": _safe_float(entry.get("wkhi")),
                            "fifty_two_week_low": _safe_float(entry.get("wklo")),
                        }
                        items.append(parsed)

                    if items:
                        raw_etfs = items
                        await market_cache.set(CACHE_KEY_NSE_ETFS, items, ttl_seconds=CACHE_TTL_ETFS)
                        await market_cache.set(SNAPSHOT_KEY_NSE_ETFS, items, ttl_seconds=86400)
            except Exception as exc:
                logger.warning("Failed fetching live NSE ETF data: %s", exc)

        # 2. Snapshot fallback
        if not raw_etfs:
            snapshot = await market_cache.get(SNAPSHOT_KEY_NSE_ETFS)
            if snapshot and isinstance(snapshot, list):
                logger.info("Serving ETF data from last known snapshot (%d items).", len(snapshot))
                raw_etfs = snapshot

        if not raw_etfs:
            return []

        # 3. Apply optional category filter
        results = raw_etfs
        if category:
            cat_norm = category.strip().lower()
            results = [
                item for item in results
                if cat_norm in item["category"].lower() or cat_norm in item["category"].lower().replace("&", "and")
            ]

        # 4. Apply optional search filter
        if search:
            q = search.strip().lower()
            results = [
                item for item in results
                if q in item["symbol"].lower() or q in item["underlying_asset"].lower()
            ]

        return results
