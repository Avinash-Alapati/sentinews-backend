"""
Asset Reference Reader Adapter.

Implements the AssetReferenceReader application port.
Provides read-only access to known company names, ticker symbols, and sectors
derived from portfolio holdings and standard Indian market indices (Nifty 50 / NSE).
"""

import logging
from typing import List, Set, Tuple
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.portfolio import HoldingORM
from app.modules.news_intelligence.application.ports import AssetReferenceReader

logger = logging.getLogger("sentinews.news_intelligence.asset_reader")

# Curated reference list of major Indian equities and sectors (Nifty 50 leaders)
DEFAULT_INDIAN_MARKET_ASSETS: List[Tuple[str, str, str]] = [
    ("RELIANCE", "Reliance Industries", "Energy"),
    ("TCS", "Tata Consultancy Services", "Technology"),
    ("HDFCBANK", "HDFC Bank", "Financial Services"),
    ("INFY", "Infosys", "Technology"),
    ("ICICIBANK", "ICICI Bank", "Financial Services"),
    ("HINDUNILVR", "Hindustan Unilever", "Consumer Goods"),
    ("ITC", "ITC Limited", "Consumer Goods"),
    ("SBIN", "State Bank of India", "Financial Services"),
    ("BHARTIARTL", "Bharti Airtel", "Telecommunications"),
    ("BAJFINANCE", "Bajaj Finance", "Financial Services"),
    ("LTI", "LTIMindtree", "Technology"),
    ("LT", "Larsen & Toubro", "Capital Goods"),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Financial Services"),
    ("AXISBANK", "Axis Bank", "Financial Services"),
    ("ASIANPAINT", "Asian Paints", "Consumer Goods"),
    ("MARUTI", "Maruti Suzuki", "Automobile"),
    ("TATAMOTORS", "Tata Motors", "Automobile"),
    ("TATASTEEL", "Tata Steel", "Metals"),
    ("SUNPHARMA", "Sun Pharmaceutical", "Healthcare"),
    ("WIPRO", "Wipro", "Technology"),
    ("NTPC", "NTPC Limited", "Utilities"),
    ("POWERGRID", "Power Grid Corporation", "Utilities"),
    ("M&M", "Mahindra & Mahindra", "Automobile"),
    ("TITAN", "Titan Company", "Consumer Goods"),
    ("ULTRACEMCO", "UltraTech Cement", "Materials"),
    ("ADANIENT", "Adani Enterprises", "Commodities"),
    ("ADANIPORTS", "Adani Ports", "Infrastructure"),
    ("COALINDIA", "Coal India", "Energy"),
    ("ONGC", "Oil and Natural Gas Corporation", "Energy"),
    ("JSWSTEEL", "JSW Steel", "Metals"),
    ("HCLTECH", "HCL Technologies", "Technology"),
    ("BAJAJFINSV", "Bajaj Finserv", "Financial Services"),
]


class DatabaseAssetReferenceReader(AssetReferenceReader):
    """
    Reads known assets from database holdings combined with baseline Indian equity references.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_known_assets(self) -> List[Tuple[str, str, str]]:
        """
        Fetches unique (symbol, name, sector) tuples from holdings in the DB,
        merged with default large-cap benchmark assets.
        """
        assets_map = {item[0].upper(): item for item in DEFAULT_INDIAN_MARKET_ASSETS}

        try:
            stmt = select(
                distinct(HoldingORM.symbol),
                HoldingORM.name,
                HoldingORM.sector,
            )
            result = await self.session.execute(stmt)
            rows = result.all()

            for sym, name, sector in rows:
                if sym:
                    sym_upper = sym.upper()
                    assets_map[sym_upper] = (
                        sym_upper,
                        name or assets_map.get(sym_upper, ("", "", ""))[1],
                        sector or assets_map.get(sym_upper, ("", "", "General"))[2],
                    )
        except Exception as exc:
            logger.warning("Failed querying holding assets from DB, using fallback list: %s", exc)

        return list(assets_map.values())
