"""
Local Symbol Master & In-Memory Indian Equities Search Engine.

Eliminates external search API latency by performing prefix and fuzzy substring
searches locally across 800+ NSE/BSE listed instruments.
"""

from typing import Dict, List, Optional
import re

from app.integrations.market.index_constituents import (
    NIFTY_TOTAL_MARKET_SYMBOLS,
    COMPANY_NAMES_MAP,
)
from app.integrations.market.indian_market_provider import IndianMarketProvider
from app.modules.market_intelligence.domain.schemas import StockSearchResult


class SymbolMaster:
    """
    In-Memory Symbol Master for instantaneous stock search across NSE & BSE.
    """

    def __init__(self):
        self._symbols: Dict[str, StockSearchResult] = {}
        self._indexed_list: List[StockSearchResult] = []
        self._load_master_data()

    def _load_master_data(self) -> None:
        """Initializes the master catalog from static constituent maps and stock names."""
        name_map = dict(IndianMarketProvider.STOCK_NAME_MAP)
        name_map.update(COMPANY_NAMES_MAP)

        for sym in NIFTY_TOTAL_MARKET_SYMBOLS:
            clean_sym = sym.strip().upper()
            comp_name = name_map.get(clean_sym, f"{clean_sym} Ltd")
            self._symbols[clean_sym] = StockSearchResult(
                symbol=clean_sym,
                name=comp_name,
                exchange="NSE",
                type="EQUITY",
            )

        # Also add popular indices
        indices = [
            ("^NSEI", "NIFTY 50", "NSE", "INDEX"),
            ("^BSESN", "SENSEX", "BSE", "INDEX"),
            ("^NSEBANK", "NIFTY BANK", "NSE", "INDEX"),
            ("^CNXIT", "NIFTY IT", "NSE", "INDEX"),
            ("^NSMIDCP", "NIFTY MIDCAP 100", "NSE", "INDEX"),
            ("^CNXSC", "NIFTY SMALLCAP 100", "NSE", "INDEX"),
        ]
        for sym, name, exch, typ in indices:
            self._symbols[sym] = StockSearchResult(
                symbol=sym,
                name=name,
                exchange=exch,
                type=typ,
            )

        self._indexed_list = list(self._symbols.values())

    def update_symbol(self, result: StockSearchResult) -> None:
        """Dynamically registers or updates a symbol in the master catalog."""
        clean_sym = result.symbol.strip().upper()
        self._symbols[clean_sym] = result
        self._indexed_list = list(self._symbols.values())

    def search(self, query: str, limit: int = 20) -> List[StockSearchResult]:
        """
        Executes fast multi-tier ranked search:
        1. Exact symbol match
        2. Symbol prefix match
        3. Name prefix match
        4. Substring match
        """
        if not query or not query.strip():
            return []

        q = query.strip().upper()
        exact_matches: List[StockSearchResult] = []
        symbol_prefix_matches: List[StockSearchResult] = []
        name_prefix_matches: List[StockSearchResult] = []
        substring_matches: List[StockSearchResult] = []

        for item in self._indexed_list:
            sym_upper = item.symbol.upper()
            name_upper = item.name.upper()

            if sym_upper == q:
                exact_matches.append(item)
            elif sym_upper.startswith(q):
                symbol_prefix_matches.append(item)
            elif name_upper.startswith(q):
                name_prefix_matches.append(item)
            elif q in sym_upper or q in name_upper:
                substring_matches.append(item)

        results = exact_matches + symbol_prefix_matches + name_prefix_matches + substring_matches
        return results[:limit]

    def get_symbol_info(self, symbol: str) -> Optional[StockSearchResult]:
        return self._symbols.get(symbol.strip().upper())

    def get_symbol(self, symbol: str) -> Optional[StockSearchResult]:
        return self.get_symbol_info(symbol)


# Global singleton instance
symbol_master = SymbolMaster()
