"""
Unit tests for In-Memory Symbol Master Search.
"""

import pytest
from app.modules.market_intelligence.domain.symbol_master import SymbolMaster
from app.modules.market_intelligence.domain.schemas import StockSearchResult


@pytest.fixture
def master():
    return SymbolMaster()


def test_symbol_master_exact_and_prefix_search(master: SymbolMaster):
    # Exact symbol search
    res = master.search("RELIANCE")
    assert len(res) >= 1
    assert res[0].symbol == "RELIANCE"
    assert "Reliance" in res[0].name

    # Prefix search
    res_prefix = master.search("INF")
    assert len(res_prefix) >= 1
    assert any(r.symbol == "INFY" for r in res_prefix)


def test_symbol_master_name_search(master: SymbolMaster):
    # Company name search
    res = master.search("Tata")
    assert len(res) >= 3
    symbols = [r.symbol for r in res]
    assert any("TATA" in s or s in ("TCS", "TTML", "TRENT") for s in symbols)


def test_symbol_master_update_and_get(master: SymbolMaster):
    new_stock = StockSearchResult(
        symbol="ZOMATO_NEW",
        name="Zomato Express Ltd",
        exchange="NSE",
        type="EQUITY",
    )
    master.update_symbol(new_stock)

    fetched = master.get_symbol_info("ZOMATO_NEW")
    assert fetched is not None
    assert fetched.name == "Zomato Express Ltd"

    search_res = master.search("ZOMATO_NEW")
    assert len(search_res) >= 1
    assert search_res[0].symbol == "ZOMATO_NEW"
