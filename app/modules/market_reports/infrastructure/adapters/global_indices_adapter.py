"""
Global Indices Market Data Adapter.

Fetches major global benchmark index quotes (US, Asia, Europe, Gift Nifty)
with high-speed concurrent async HTTP chart queries and multi-tier fallback:
Async Direct Chart API -> Finnhub -> Snapshot Cache.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import IndexPoint
from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import outbound_limiter

logger = logging.getLogger("sentinews.market_reports.global_indices")

DEFAULT_GLOBAL_INDICES = [
    {"symbol": "^GSPC", "name": "S&P 500", "region": "US"},
    {"symbol": "^IXIC", "name": "Nasdaq Composite", "region": "US"},
    {"symbol": "^DJI", "name": "Dow Jones", "region": "US"},
    {"symbol": "^RUT", "name": "Russell 2000", "region": "US"},
    {"symbol": "^N225", "name": "Nikkei 225", "region": "Asia"},
    {"symbol": "^HSI", "name": "Hang Seng", "region": "Asia"},
    {"symbol": "000001.SS", "name": "Shanghai Composite", "region": "Asia"},
    {"symbol": "^FTSE", "name": "FTSE 100", "region": "Europe"},
    {"symbol": "^GDAXI", "name": "DAX", "region": "Europe"},
    {"symbol": "^FCHI", "name": "CAC 40", "region": "Europe"},
    {"symbol": "^NSEI", "name": "GIFT Nifty", "region": "GIFT City"},
]

CACHE_KEY_GLOBAL_INDICES = "market_reports:adapter:global_indices"
CACHE_TTL_SECONDS = 300  # 5 minutes
SNAPSHOT_KEY_GLOBAL_INDICES = "market_reports:snapshot:global_indices"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class GlobalIndicesAdapter:
    """
    Adapter responsible for querying major global market indices in real time.
    """

    def __init__(
        self,
        finnhub_fallback: Optional[FinnhubClient] = None,
        timeout: float = 8.0,
    ):
        self.finnhub_fallback = finnhub_fallback or FinnhubClient()
        self.timeout = timeout

    async def get_major_global_indices(self) -> List[IndexPoint]:
        """
        Fetches quotes for major global indices.
        Returns a list of IndexPoint entities.
        """
        # 1. Check cache first
        cached = await market_cache.get(CACHE_KEY_GLOBAL_INDICES)
        if cached and isinstance(cached, list) and len(cached) > 0:
            return [IndexPoint(**item) for item in cached]

        # 2. Try Primary: High-speed async chart query
        results: List[IndexPoint] = []
        try:
            results = await self._fetch_from_yfinance()
            if results:
                serialized = [r.__dict__ for r in results]
                await market_cache.set(CACHE_KEY_GLOBAL_INDICES, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                await market_cache.set(SNAPSHOT_KEY_GLOBAL_INDICES, serialized, ttl_seconds=86400)
                return results
        except Exception as exc:
            logger.warning("Primary global indices fetch failed (%s). Attempting Finnhub fallback.", exc)

        # 3. Try Secondary Fallback: Finnhub
        try:
            finnhub_raw = await self.finnhub_fallback.get_global_indices()
            if finnhub_raw:
                for item in finnhub_raw:
                    lp = float(item.get("last_price", 0.0))
                    if lp > 0:
                        results.append(
                            IndexPoint(
                                symbol=item.get("symbol", ""),
                                name=item.get("name", item.get("symbol", "")),
                                last_price=round(lp, 2),
                                change=round(float(item.get("change", 0.0)), 2),
                                change_percent=round(float(item.get("change_percent", 0.0)), 2),
                            )
                        )
                if results:
                    serialized = [r.__dict__ for r in results]
                    await market_cache.set(CACHE_KEY_GLOBAL_INDICES, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                    await market_cache.set(SNAPSHOT_KEY_GLOBAL_INDICES, serialized, ttl_seconds=86400)
                    return results
        except Exception as exc:
            logger.warning("Finnhub global indices fallback failed: %s", exc)

        # 4. Try Tertiary: Last successful snapshot fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_GLOBAL_INDICES)
        if snapshot and isinstance(snapshot, list):
            logger.info("Serving global indices from last known successful snapshot.")
            return [IndexPoint(**item) for item in snapshot]

        logger.error("All global indices providers failed; returning empty list.")
        return []

    async def _fetch_from_yfinance(self) -> List[IndexPoint]:
        """Queries fast async chart endpoints."""
        return await self._fetch_from_chart_api()

    async def _fetch_from_chart_api(self) -> List[IndexPoint]:
        """Queries fast async chart endpoints with concurrency and failover."""
        async with outbound_limiter.pace("yfinance"):
            async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=self.timeout) as client:
                async def _fetch_single(target: Dict[str, str]) -> Optional[IndexPoint]:
                    sym = target["symbol"]
                    name = target["name"]
                    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
                        url = f"https://{host}/v8/finance/chart/{sym}?interval=1d&range=5d"
                        try:
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                data = resp.json()
                                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                                last_price = float(meta.get("regularMarketPrice") or 0.0)
                                prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0)

                                if last_price <= 0:
                                    # Check timestamps and close quotes
                                    indicators = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0]
                                    closes = [c for c in indicators.get("close", []) if c is not None]
                                    if closes:
                                        last_price = float(closes[-1])
                                        if len(closes) > 1:
                                            prev_close = float(closes[-2])

                                if last_price > 0:
                                    change = round(last_price - prev_close if prev_close > 0 else 0.0, 2)
                                    change_pct = round((change / prev_close * 100.0) if prev_close > 0 else 0.0, 2)
                                    return IndexPoint(
                                        symbol=sym,
                                        name=name,
                                        last_price=round(last_price, 2),
                                        change=change,
                                        change_percent=change_pct,
                                    )
                        except Exception as ex:
                            logger.debug("Chart query error for %s on %s: %s", sym, host, ex)
                    return None

                results = await asyncio.gather(*[_fetch_single(tgt) for tgt in DEFAULT_GLOBAL_INDICES])
                return [r for r in results if r is not None]
