"""
Commodities Market Data Adapter.

Fetches key international commodity benchmark futures (Brent Crude, WTI Crude,
Gold, Silver, Natural Gas, Copper, Platinum) via high-speed async HTTP chart queries.

Explicitly labels quotes as 'international_benchmark' to ensure users and algorithms
do not conflate these prices with domestic MCX physical settlement rates.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional
import httpx

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import CommodityItem
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import outbound_limiter

logger = logging.getLogger("sentinews.market_reports.commodities")

TARGET_COMMODITIES = [
    {"symbol": "BZ=F", "name": "Brent Crude Oil", "unit": "USD/bbl"},
    {"symbol": "CL=F", "name": "WTI Crude Oil", "unit": "USD/bbl"},
    {"symbol": "GC=F", "name": "Gold", "unit": "USD/t oz"},
    {"symbol": "SI=F", "name": "Silver", "unit": "USD/t oz"},
    {"symbol": "NG=F", "name": "Natural Gas", "unit": "USD/MMBtu"},
    {"symbol": "HG=F", "name": "Copper", "unit": "USD/lb"},
    {"symbol": "PL=F", "name": "Platinum", "unit": "USD/t oz"},
]

CACHE_KEY_COMMODITIES = "market_reports:adapter:commodities"
SNAPSHOT_KEY_COMMODITIES = "market_reports:snapshot:commodities"
CACHE_TTL_SECONDS = 300  # 5 minutes

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class CommoditiesAdapter:
    """
    Adapter providing commodity futures quotes with international benchmark labeling.
    """

    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    async def get_commodities(self) -> List[CommodityItem]:
        """
        Retrieves commodity benchmark quotes.
        Returns a list of CommodityItem entities.
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_COMMODITIES)
        if cached and isinstance(cached, list) and len(cached) > 0:
            return [CommodityItem(**item) for item in cached]

        # 2. Try fast async HTTP fetch
        try:
            results = await self._fetch_from_yfinance()
            if results:
                serialized = [r.__dict__ for r in results]
                await market_cache.set(CACHE_KEY_COMMODITIES, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                await market_cache.set(SNAPSHOT_KEY_COMMODITIES, serialized, ttl_seconds=86400)
                return results
        except Exception as exc:
            logger.warning("Commodities fetch failed (%s). Attempting snapshot fallback.", exc)

        # 3. Snapshot Fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_COMMODITIES)
        if snapshot and isinstance(snapshot, list):
            logger.info("Serving commodities from last known successful snapshot.")
            return [CommodityItem(**item) for item in snapshot]

        return []

    async def _fetch_from_yfinance(self) -> List[CommodityItem]:
        """Queries fast async chart endpoints."""
        return await self._fetch_from_chart_api()

    async def _fetch_from_chart_api(self) -> List[CommodityItem]:
        async with outbound_limiter.pace("yfinance"):
            async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=self.timeout) as client:
                async def _fetch_single(target: Dict[str, str]) -> Optional[CommodityItem]:
                    sym = target["symbol"]
                    name = target["name"]
                    unit = target["unit"]
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
                                    indicators = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0]
                                    closes = [c for c in indicators.get("close", []) if c is not None]
                                    if closes:
                                        last_price = float(closes[-1])
                                        if len(closes) > 1:
                                            prev_close = float(closes[-2])

                                if last_price > 0:
                                    change = round(last_price - prev_close if prev_close > 0 else 0.0, 2)
                                    change_percent = round((change / prev_close * 100.0) if prev_close > 0 else 0.0, 2)
                                    return CommodityItem(
                                        symbol=sym,
                                        name=name,
                                        last_price=round(last_price, 2),
                                        change=change,
                                        change_percent=change_percent,
                                        unit=unit,
                                        source="international_benchmark",
                                    )
                        except Exception as ex:
                            logger.debug("Commodity quote failed for %s on %s: %s", sym, host, ex)
                    return None

                results = await asyncio.gather(*[_fetch_single(tgt) for tgt in TARGET_COMMODITIES])
                return [r for r in results if r is not None]
