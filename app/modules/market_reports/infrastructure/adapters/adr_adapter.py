"""
Indian ADRs (American Depository Receipts) Market Data Adapter.

Tracks overnight US trading performance of Indian company ADRs listed on NYSE / NASDAQ
via high-speed concurrent async HTTP chart queries.

VERIFIED ACTIVE ADR LIST:
- INFY (Infosys - NYSE)
- WIT (Wipro - NYSE)
- IBN (ICICI Bank - NYSE)
- RDY (Dr. Reddy's Laboratories - NYSE)
- MMYT (MakeMyTrip - NASDAQ)
- HDB (HDFC Bank - NYSE)

Note: TTM (Tata Motors ADR) was delisted from NYSE in January 2023.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional
import httpx

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import ADRItem
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import outbound_limiter

logger = logging.getLogger("sentinews.market_reports.adr_adapter")

ACTIVE_INDIAN_ADRS = [
    {"symbol": "INFY", "company_name": "Infosys ADR", "exchange": "NYSE"},
    {"symbol": "WIT", "company_name": "Wipro ADR", "exchange": "NYSE"},
    {"symbol": "IBN", "company_name": "ICICI Bank ADR", "exchange": "NYSE"},
    {"symbol": "RDY", "company_name": "Dr. Reddy's Laboratories ADR", "exchange": "NYSE"},
    {"symbol": "MMYT", "company_name": "MakeMyTrip ADR", "exchange": "NASDAQ"},
    {"symbol": "HDB", "company_name": "HDFC Bank ADR", "exchange": "NYSE"},
]

CACHE_KEY_ADRS = "market_reports:adapter:indian_adrs"
SNAPSHOT_KEY_ADRS = "market_reports:snapshot:indian_adrs"
CACHE_TTL_SECONDS = 300  # 5 minutes

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class IndianADRAdapter:
    """
    Adapter querying active Indian ADRs listed on US stock exchanges in real time.
    """

    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    async def get_indian_adrs(self) -> List[ADRItem]:
        """
        Retrieves performance metrics for Indian company ADRs.
        Returns a list of ADRItem entities.
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_ADRS)
        if cached and isinstance(cached, list) and len(cached) > 0:
            return [ADRItem(**item) for item in cached]

        # 2. Try fast async HTTP fetch
        try:
            results = await self._fetch_from_yfinance()
            if results:
                serialized = [r.__dict__ for r in results]
                await market_cache.set(CACHE_KEY_ADRS, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                await market_cache.set(SNAPSHOT_KEY_ADRS, serialized, ttl_seconds=86400)
                return results
        except Exception as exc:
            logger.warning("Indian ADRs fetch failed (%s). Attempting snapshot fallback.", exc)

        # 3. Snapshot Fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_ADRS)
        if snapshot and isinstance(snapshot, list) and len(snapshot) > 0:
            logger.info("Serving Indian ADRs from last known snapshot.")
            return [ADRItem(**item) for item in snapshot]

        return []

    async def _fetch_from_yfinance(self) -> List[ADRItem]:
        """Queries fast async chart endpoints."""
        return await self._fetch_from_chart_api()

    async def _fetch_from_chart_api(self) -> List[ADRItem]:
        async with outbound_limiter.pace("yfinance"):
            async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=self.timeout) as client:
                async def _fetch_single(target: Dict[str, str]) -> Optional[ADRItem]:
                    sym = target["symbol"]
                    company = target["company_name"]
                    exchange = target["exchange"]
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
                                    return ADRItem(
                                        symbol=sym,
                                        company_name=company,
                                        last_price=round(last_price, 2),
                                        change=change,
                                        change_percent=change_percent,
                                        exchange=exchange,
                                    )
                        except Exception as ex:
                            logger.debug("ADR query failed for %s on %s: %s", sym, host, ex)
                    return None

                results = await asyncio.gather(*[_fetch_single(tgt) for tgt in ACTIVE_INDIAN_ADRS])
                return [r for r in results if r is not None]
