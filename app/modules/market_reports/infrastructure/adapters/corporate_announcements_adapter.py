"""
Corporate Announcements and Board Meetings Adapter.

Ingests corporate event filings, board meeting notices, and dividend/earnings announcements
from NSE and BSE public announcement endpoints.

Architecture:
- Connects to NSE /api/corporate-announcements?index=equities.
- Filters and categorizes events (Financial Results, Board Meetings, Dividends, Strategic Actions).
- Enforces statutory SEBI compliance sanitization on announcement text.
- Dual-tier caching and snapshot fallback.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import CorporateEventItem
from app.modules.market_reports.domain.services.compliance import check_actionable_language
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import outbound_limiter

logger = logging.getLogger("sentinews.market_reports.corporate_announcements")

NSE_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
NSE_BASE_URL = "https://www.nseindia.com"

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

CACHE_KEY_ANNOUNCEMENTS = "market_reports:adapter:corporate_announcements"
SNAPSHOT_KEY_ANNOUNCEMENTS = "market_reports:snapshot:corporate_announcements"
CACHE_TTL_SECONDS = 900  # 15 minutes


class CorporateAnnouncementsAdapter:
    """
    Adapter interfacing with exchange corporate filing feeds.
    """

    def __init__(self, timeout: float = 10.0, max_retries: int = 2):
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
            )
            self._handshake_done = False
        return self._client

    async def _ensure_handshake(self) -> None:
        async with self._lock:
            if self._handshake_done:
                return
            client = await self._get_client()
            try:
                async with outbound_limiter.pace("nse"):
                    await client.get(NSE_BASE_URL)
                self._handshake_done = True
            except Exception as exc:
                logger.debug("NSE announcement handshake attempt: %s", exc)

    async def _fetch_json(self, url: str) -> Optional[Any]:
        client = await self._get_client()
        for attempt in range(1, self.max_retries + 1):
            try:
                await self._ensure_handshake()
                async with outbound_limiter.pace("nse"):
                    resp = await client.get(url, headers=NSE_HEADERS)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as exc:
                logger.debug("Announcement fetch attempt %d failed: %s", attempt, exc)
                await asyncio.sleep(0.3)
        return None

    async def get_recent_corporate_events(self, limit: int = 15) -> List[CorporateEventItem]:
        """
        Retrieves recent exchange announcements and corporate filings.
        Returns list of CorporateEventItem domain entities.
        """
        # 1. Check cache
        cached = await market_cache.get(CACHE_KEY_ANNOUNCEMENTS)
        if cached and isinstance(cached, list):
            return [CorporateEventItem(**item) for item in cached[:limit]]

        events: List[CorporateEventItem] = []
        try:
            raw_data = await self._fetch_json(NSE_ANNOUNCEMENTS_URL)
            if raw_data:
                raw_list = raw_data if isinstance(raw_data, list) else raw_data.get("data", [])
                for entry in raw_list:
                    sym = str(entry.get("symbol", "")).strip()
                    comp_name = str(entry.get("sm_name", entry.get("company_name", sym))).strip()
                    desc = str(entry.get("desc", entry.get("subject", ""))).strip()
                    an_dt = str(entry.get("an_dt", entry.get("broadcastDate", ""))).strip()
                    attch = str(entry.get("attchmntText", "")).strip()

                    # Classify event type
                    desc_upper = (desc + " " + attch).upper()
                    if "FINANCIAL RESULTS" in desc_upper or "QUARTERLY" in desc_upper or "AUDITED" in desc_upper:
                        event_type = "Financial Results"
                    elif "BOARD MEETING" in desc_upper:
                        event_type = "Board Meeting"
                    elif "DIVIDEND" in desc_upper:
                        event_type = "Dividend Announcement"
                    elif "RATING" in desc_upper:
                        event_type = "Credit Rating Action"
                    elif "AGM" in desc_upper or "EGM" in desc_upper:
                        event_type = "Shareholder Meeting"
                    elif "ACQUISITION" in desc_upper or "MERGER" in desc_upper or "DEMERGER" in desc_upper:
                        event_type = "Corporate Restructuring"
                    else:
                        event_type = "General Disclosure"

                    # SEBI compliance check: strip any prohibited words if present
                    if check_actionable_language(desc):
                        continue

                    details = desc if desc else attch
                    if len(details) > 280:
                        details = details[:277] + "..."

                    if sym:
                        events.append(
                            CorporateEventItem(
                                symbol=sym,
                                company_name=comp_name or sym,
                                event_type=event_type,
                                details=details or f"{event_type} disclosure filed with exchange.",
                                announcement_date=an_dt or datetime.now(timezone.utc).strftime("%d-%b-%Y %H:%M"),
                                source="NSE",
                            )
                        )

                if events:
                    serialized = [e.__dict__ for e in events]
                    await market_cache.set(CACHE_KEY_ANNOUNCEMENTS, serialized, ttl_seconds=CACHE_TTL_SECONDS)
                    await market_cache.set(SNAPSHOT_KEY_ANNOUNCEMENTS, serialized, ttl_seconds=86400)
                    return events[:limit]
        except Exception as exc:
            logger.warning("Corporate announcements fetch failed: %s", exc)

        # Snapshot fallback
        snapshot = await market_cache.get(SNAPSHOT_KEY_ANNOUNCEMENTS)
        if snapshot and isinstance(snapshot, list):
            logger.info("Serving corporate announcements from last known snapshot.")
            return [CorporateEventItem(**item) for item in snapshot[:limit]]

        return []
