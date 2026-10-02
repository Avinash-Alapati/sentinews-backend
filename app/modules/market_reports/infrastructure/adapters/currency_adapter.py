"""
Currency Pairs (INR-Relevant) Market Data Adapter.

Retrieves major INR-relevant currency pairs (USD/INR, EUR/INR, GBP/INR, JPY/INR, AED/INR, DXY)
via fast async HTTP chart queries, with fallback to public exchange rate API and snapshot caching.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional
import httpx

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import CurrencyPairItem
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import outbound_limiter

logger = logging.getLogger("sentinews.market_reports.currencies")

TARGET_CURRENCY_PAIRS = [
    {"pair": "USD/INR", "ticker": "USDINR=X"},
    {"pair": "EUR/INR", "ticker": "EURINR=X"},
    {"pair": "GBP/INR", "ticker": "GBPINR=X"},
    {"pair": "JPY/INR", "ticker": "JPYINR=X"},
    {"pair": "AED/INR", "ticker": "AEDINR=X"},
    {"pair": "DXY (Dollar Index)", "ticker": "DX-Y.NYB"},
]

CACHE_KEY_CURRENCIES = "market_reports:adapter:currencies"
SNAPSHOT_KEY_CURRENCIES = "market_reports:snapshot:currencies"
CACHE_TTL_SECONDS = 300  # 5 minutes

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class CurrencyAdapter:
    """
    Adapter querying INR-relevant exchange rates with real-time accuracy.
    """

    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    async def get_inr_currency_pairs(self) -> List[CurrencyPairItem]:
        """
        Retrieves INR currency pair rates.
        Returns a list of CurrencyPairItem entities.
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_CURRENCIES)
        if cached and isinstance(cached, list) and len(cached) > 0:
            return [CurrencyPairItem(**item) for item in cached]

        # 2. Try Primary fast async HTTP fetch
        try:
            results = await self._fetch_from_yfinance()
            if results:
                serialized = [r.__dict__ for r in results]
                await market_cache.set(CACHE_KEY_CURRENCIES, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                await market_cache.set(SNAPSHOT_KEY_CURRENCIES, serialized, ttl_seconds=86400)
                return results
        except Exception as exc:
            logger.warning("Primary currency rates fetch failed (%s). Attempting OpenER fallback.", exc)

        # 3. Secondary Fallback: Free Open Exchange Rates API
        try:
            results = await self._fetch_from_open_er_api()
            if results:
                serialized = [r.__dict__ for r in results]
                await market_cache.set(CACHE_KEY_CURRENCIES, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                await market_cache.set(SNAPSHOT_KEY_CURRENCIES, serialized, ttl_seconds=86400)
                return results
        except Exception as exc:
            logger.warning("Open ER API fallback failed: %s", exc)

        # 4. Snapshot Fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_CURRENCIES)
        if snapshot and isinstance(snapshot, list):
            logger.info("Serving currencies from last known successful snapshot.")
            return [CurrencyPairItem(**item) for item in snapshot]

        return []

    async def _fetch_from_yfinance(self) -> List[CurrencyPairItem]:
        """Queries fast async chart endpoints."""
        return await self._fetch_from_chart_api()

    async def _fetch_from_chart_api(self) -> List[CurrencyPairItem]:
        async with outbound_limiter.pace("yfinance"):
            async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=self.timeout) as client:
                async def _fetch_single(target: Dict[str, str]) -> Optional[CurrencyPairItem]:
                    pair = target["pair"]
                    sym = target["ticker"]
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
                                    change = round(last_price - prev_close if prev_close > 0 else 0.0, 4)
                                    change_percent = round((change / prev_close * 100.0) if prev_close > 0 else 0.0, 2)
                                    return CurrencyPairItem(
                                        pair=pair,
                                        last_price=round(last_price, 4),
                                        change=change,
                                        change_percent=change_percent,
                                        source="realtime_chart",
                                    )
                        except Exception as ex:
                            logger.debug("Currency query failed for %s on %s: %s", sym, host, ex)
                    return None

                results = await asyncio.gather(*[_fetch_single(tgt) for tgt in TARGET_CURRENCY_PAIRS])
                return [r for r in results if r is not None]

    async def _fetch_from_open_er_api(self) -> List[CurrencyPairItem]:
        """Free public exchange rates API fallback."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            if resp.status_code == 200:
                data = resp.json()
                rates = data.get("rates", {})
                usd_inr = float(rates.get("INR", 83.5))
                eur = float(rates.get("EUR", 0.92))
                gbp = float(rates.get("GBP", 0.79))
                jpy = float(rates.get("JPY", 155.0))
                aed = float(rates.get("AED", 3.6725))

                eur_inr = usd_inr / eur if eur > 0 else 0.0
                gbp_inr = usd_inr / gbp if gbp > 0 else 0.0
                jpy_inr = usd_inr / jpy if jpy > 0 else 0.0
                aed_inr = usd_inr / aed if aed > 0 else 0.0

                return [
                    CurrencyPairItem(pair="USD/INR", last_price=round(usd_inr, 4), change=0.0, change_percent=0.0, source="open_er_api"),
                    CurrencyPairItem(pair="EUR/INR", last_price=round(eur_inr, 4), change=0.0, change_percent=0.0, source="open_er_api"),
                    CurrencyPairItem(pair="GBP/INR", last_price=round(gbp_inr, 4), change=0.0, change_percent=0.0, source="open_er_api"),
                    CurrencyPairItem(pair="JPY/INR", last_price=round(jpy_inr, 4), change=0.0, change_percent=0.0, source="open_er_api"),
                    CurrencyPairItem(pair="AED/INR", last_price=round(aed_inr, 4), change=0.0, change_percent=0.0, source="open_er_api"),
                ]
        return []
