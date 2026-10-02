"""
Market Report News Intelligence Adapter.

Derives 'stocks_in_news' and 'market_news' sections directly from the existing
SentiNews News Intelligence module (LiveNewsService and RSS ingestion pipeline).

SEBI Regulatory Rules:
- All headline summaries and descriptions are strictly factual and non-actionable.
- PROHIBITED terms (buy now, target price, multibagger, stop loss, etc.) are filtered out.
- Zero extra external news dependencies added.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional, Tuple

from app.modules.market_reports.domain.entities import HeadlineItem, StockInNewsItem
from app.modules.market_reports.domain.services.compliance import (
    check_actionable_language,
    is_headline_compliant,
)
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.live_news_service import (
    LiveNewsService,
    live_news_service,
)

logger = logging.getLogger("sentinews.market_reports.news_adapter")

# Mapping of primary NSE ticker symbols to clean company display names
TICKER_NAME_MAP = {
    "RELIANCE": "Reliance Industries Ltd",
    "TCS": "Tata Consultancy Services Ltd",
    "HDFCBANK": "HDFC Bank Ltd",
    "INFY": "Infosys Ltd",
    "ICICIBANK": "ICICI Bank Ltd",
    "SBIN": "State Bank of India",
    "BHARTIARTL": "Bharti Airtel Ltd",
    "ITC": "ITC Ltd",
    "TATAMOTORS": "Tata Motors Ltd",
    "KOTAKBANK": "Kotak Mahindra Bank Ltd",
    "LT": "Larsen & Toubro Ltd",
    "AXISBANK": "Axis Bank Ltd",
    "MARUTI": "Maruti Suzuki India Ltd",
    "SUNPHARMA": "Sun Pharmaceutical Industries Ltd",
    "TITAN": "Titan Company Ltd",
    "BAJFINANCE": "Bajaj Finance Ltd",
    "WIPRO": "Wipro Ltd",
    "ADANIENT": "Adani Enterprises Ltd",
    "ADANIPORTS": "Adani Ports & SEZ Ltd",
    "ADANIGREEN": "Adani Green Energy Ltd",
    "HCLTECH": "HCL Technologies Ltd",
    "ULTRACEMCO": "UltraTech Cement Ltd",
    "ASIANPAINT": "Asian Paints Ltd",
    "NTPC": "NTPC Ltd",
    "POWERGRID": "Power Grid Corporation of India Ltd",
    "ONGC": "Oil & Natural Gas Corporation Ltd",
    "TATASTEEL": "Tata Steel Ltd",
    "JSWSTEEL": "JSW Steel Ltd",
    "HINDALCO": "Hindalco Industries Ltd",
    "COALINDIA": "Coal India Ltd",
    "BAJAJFINSV": "Bajaj Finserv Ltd",
    "NESTLEIND": "Nestle India Ltd",
    "TECHM": "Tech Mahindra Ltd",
    "HINDUNILVR": "Hindustan Unilever Ltd",
    "DRREDDY": "Dr. Reddy's Laboratories Ltd",
    "CIPLA": "Cipla Ltd",
    "DIVISLAB": "Divi's Laboratories Ltd",
    "APOLLOHOSP": "Apollo Hospitals Enterprise Ltd",
    "M&M": "Mahindra & Mahindra Ltd",
    "BAJAJ-AUTO": "Bajaj Auto Ltd",
    "HEROMOTOCO": "Hero MotoCorp Ltd",
    "EICHERMOT": "Eicher Motors Ltd",
    "TVSMOTOR": "TVS Motor Company Ltd",
    "TATAPOWER": "Tata Power Company Ltd",
    "IOC": "Indian Oil Corporation Ltd",
    "BPCL": "Bharat Petroleum Corporation Ltd",
    "GAIL": "GAIL (India) Ltd",
    "BEL": "Bharat Electronics Ltd",
    "HAL": "Hindustan Aeronautics Ltd",
    "SIEMENS": "Siemens Ltd",
    "ABB": "ABB India Ltd",
    "DLF": "DLF Ltd",
    "ZOMATO": "Zomato Ltd",
    "PAYTM": "One97 Communications Ltd",
    "JIOFIN": "Jio Financial Services Ltd",
}


class MarketReportNewsAdapter:
    """
    Adapter bridging News Intelligence RSS pipeline with Market Reports generation.
    """

    def __init__(self, news_service: Optional[LiveNewsService] = None):
        self.news_service = news_service or live_news_service

    async def get_stocks_in_news(
        self,
        limit: int = 8,
        window_hours: int = 24,
    ) -> List[StockInNewsItem]:
        """
        Derives top stocks in the news by identifying trending articles with equity ticker tags.
        Extracts concise, factual descriptions adhering to SEBI guidelines.
        """
        try:
            articles = await self.news_service.get_trending_articles(limit=60)
            if not articles:
                articles = await self.news_service.get_active_articles(limit=60)

            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            stocks_map: dict[str, StockInNewsItem] = {}

            # First pass: articles within time window
            for art in articles:
                if art.published_at and art.published_at.tzinfo is None:
                    pub = art.published_at.replace(tzinfo=timezone.utc)
                else:
                    pub = art.published_at

                if pub and pub < cutoff:
                    continue

                if not is_headline_compliant(art.title) or check_actionable_language(art.summary):
                    continue

                for sym in art.symbols:
                    sym_clean = sym.upper().strip()
                    if sym_clean in ("NIFTY", "SENSEX", "BANKNIFTY", "MARKET", "INDIA"):
                        continue

                    if sym_clean not in stocks_map:
                        company_name = TICKER_NAME_MAP.get(sym_clean, sym_clean)
                        desc = art.summary if art.summary and len(art.summary) > 20 else art.title
                        if len(desc) > 200:
                            desc = desc[:197] + "..."

                        stocks_map[sym_clean] = StockInNewsItem(
                            symbol=sym_clean,
                            company_name=company_name,
                            description=desc,
                            source_headline=art.title,
                            source_url=art.url,
                        )

                    if len(stocks_map) >= limit:
                        break
                if len(stocks_map) >= limit:
                    break

            # Second pass: if fewer than limit found, include recent compliant active articles
            if len(stocks_map) < limit:
                for art in articles:
                    if not is_headline_compliant(art.title) or check_actionable_language(art.summary):
                        continue
                    for sym in art.symbols:
                        sym_clean = sym.upper().strip()
                        if sym_clean in ("NIFTY", "SENSEX", "BANKNIFTY", "MARKET", "INDIA"):
                            continue
                        if sym_clean not in stocks_map:
                            company_name = TICKER_NAME_MAP.get(sym_clean, sym_clean)
                            desc = art.summary if art.summary and len(art.summary) > 20 else art.title
                            if len(desc) > 200:
                                desc = desc[:197] + "..."
                            stocks_map[sym_clean] = StockInNewsItem(
                                symbol=sym_clean,
                                company_name=company_name,
                                description=desc,
                                source_headline=art.title,
                                source_url=art.url,
                            )
                        if len(stocks_map) >= limit:
                            break
                    if len(stocks_map) >= limit:
                        break

            return list(stocks_map.values())
        except Exception as exc:
            logger.warning("Failed deriving stocks in news from LiveNewsService: %s", exc)
            return []

    async def get_market_news(
        self,
        limit: int = 10,
        window_hours: int = 24,
    ) -> List[HeadlineItem]:
        """
        Derives macroeconomic and broad market-impacting headlines.
        """
        try:
            articles = await self.news_service.get_trending_articles(limit=40)
            if not articles:
                articles = await self.news_service.get_active_articles(limit=40)

            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            headlines: List[HeadlineItem] = []
            seen_titles = set()

            # First pass: within window
            for art in articles:
                if art.published_at and art.published_at.tzinfo is None:
                    pub = art.published_at.replace(tzinfo=timezone.utc)
                else:
                    pub = art.published_at

                if pub and pub < cutoff:
                    continue

                if not is_headline_compliant(art.title):
                    continue

                if art.title in seen_titles:
                    continue
                seen_titles.add(art.title)

                headlines.append(
                    HeadlineItem(
                        headline=art.title,
                        source=art.source or "Financial News",
                        url=art.url or "",
                        published_at=pub.isoformat() if pub else datetime.now(timezone.utc).isoformat(),
                    )
                )

                if len(headlines) >= limit:
                    break

            # Second pass fallback
            if len(headlines) < limit:
                for art in articles:
                    if not is_headline_compliant(art.title) or art.title in seen_titles:
                        continue
                    seen_titles.add(art.title)
                    pub = art.published_at.replace(tzinfo=timezone.utc) if (art.published_at and art.published_at.tzinfo is None) else art.published_at
                    headlines.append(
                        HeadlineItem(
                            headline=art.title,
                            source=art.source or "Financial News",
                            url=art.url or "",
                            published_at=pub.isoformat() if pub else datetime.now(timezone.utc).isoformat(),
                        )
                    )
                    if len(headlines) >= limit:
                        break

            return headlines
        except Exception as exc:
            logger.warning("Failed deriving market news from LiveNewsService: %s", exc)
            return []
