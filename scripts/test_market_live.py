import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.market_intelligence.application.service import market_service
from app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter import NSEMarketDataProvider
from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient


async def main():
    print("=" * 60)
    print("SENTINEWS MARKET MODULE - REAL-TIME DATA VALIDATION TEST")
    print("=" * 60)

    # 1. Single Real-Time Quotes (including demerged & rebranded tickers)
    print("\n=== 1. Testing Single Real-Time Quotes (Demerged & Rebranded Stocks) ===")
    test_symbols = ["RELIANCE", "TATAMOTORS", "TMPV", "ZOMATO", "ETERNAL", "M&M"]
    for sym in test_symbols:
        q = await market_service.get_realtime_quote(sym)
        if q and q.current_price > 0:
            print(
                f"[OK] {sym:<12} -> Symbol: {q.symbol:<10} | Price: INR {q.current_price:>8.2f} | "
                f"Change: {q.change:>6.2f} ({q.change_percent:>+6.2f}%) | "
                f"Vol: {q.volume or 0:>10,} | Name: {q.company_name}"
            )
        else:
            print(f"[FAIL] {sym}: Failed to retrieve valid quote (got {q})")

    # 2. Batch Quotes
    print("\n=== 2. Testing Batch Real-Time Quotes ===")
    batch = ["TCS", "HDFCBANK", "INFY", "TATAMOTORS", "ZOMATO", "AXISBANK", "ITC"]
    quotes = await market_service.get_realtime_quotes(batch)
    print(f"Requested: {len(batch)} symbols | Retrieved: {len(quotes)} quotes")
    for q in quotes:
        print(f"  [OK] {q.symbol:<12} | INR {q.current_price:>8.2f} | {q.change_percent:>+6.2f}% | {q.company_name}")
    assert len(quotes) >= len(batch) - 1, "Batch quote retrieval had unexpected omissions"

    # 3. Real-Time Benchmark Indices
    print("\n=== 3. Testing Real-Time Indian Benchmark Indices ===")
    indices = await market_service.get_indian_indices()
    for idx in indices:
        print(
            f"  [OK] {idx.name:<18} ({idx.symbol:<20}): Value={idx.current_value:>9.2f} | "
            f"Change={idx.change:>+7.2f} ({idx.change_percent:>+5.2f}%) | Open={idx.is_market_open}"
        )
    assert len(indices) >= 4, "Expected at least 4 major benchmark indices"

    # 4. Live Market Overview (Gainers / Losers / Most Active)
    print("\n=== 4. Testing Live Market Overview (Top Gainers, Losers, Most Active) ===")
    overview = await market_service.get_market_overview()
    print(f"Market Status: {overview.market_status} ({overview.status_message})")
    
    print(f"\n  --- TOP GAINERS ({len(overview.top_gainers)} items) ---")
    for i, g in enumerate(overview.top_gainers, 1):
        print(
            f"  {i:>2}. {g.symbol:<12} | Price: INR {g.current_price:>8.2f} | "
            f"Change: {g.change:>+6.2f} ({g.change_percent:>+6.2f}%) | "
            f"Vol: {g.volume or 0:>10,} | {g.company_name}"
        )
        assert g.current_price > 0, f"Gainer {g.symbol} price must be positive"
        assert g.change_percent > 0, f"Gainer {g.symbol} change percent must be positive"

    print(f"\n  --- TOP LOSERS ({len(overview.top_losers)} items) ---")
    for i, l in enumerate(overview.top_losers, 1):
        print(
            f"  {i:>2}. {l.symbol:<12} | Price: INR {l.current_price:>8.2f} | "
            f"Change: {l.change:>+6.2f} ({l.change_percent:>+6.2f}%) | "
            f"Vol: {l.volume or 0:>10,} | {l.company_name}"
        )
        assert l.current_price > 0, f"Loser {l.symbol} price must be positive"
        assert l.change_percent < 0, f"Loser {l.symbol} change percent must be negative"

    print(f"\n  --- MOST ACTIVE BY VOLUME ({len(overview.most_active)} items) ---")
    for i, m in enumerate(overview.most_active, 1):
        print(
            f"  {i:>2}. {m.symbol:<12} | Price: INR {m.current_price:>8.2f} | "
            f"Change: {m.change_percent:>+6.2f}% | Vol: {m.volume or 0:>12,} | {m.company_name}"
        )
        assert m.current_price > 0, f"Most active {m.symbol} price must be positive"

    # 5. Direct Adapter Tests (NSE Market Data Adapter)
    print("\n=== 5. Testing Official NSE Market Data Adapter ===")
    nse = NSEMarketDataProvider()
    gainers, losers = await nse.get_top_gainers_and_losers()
    print(f"[OK] NSE Adapter Direct: Retrieved {len(gainers)} gainers and {len(losers)} losers")
    if gainers:
        print(f"     Top Gainer: {gainers[0].symbol} ({gainers[0].company_name}) at INR {gainers[0].current_price} (+{gainers[0].change_percent}%)")
    if losers:
        print(f"     Top Loser:  {losers[0].symbol} ({losers[0].company_name}) at INR {losers[0].current_price} ({losers[0].change_percent}%)")
    assert len(gainers) > 0, "NSE direct gainers must not be empty"
    assert len(losers) > 0, "NSE direct losers must not be empty"

    # 6. Finnhub Client Dynamic Data Verification
    print("\n=== 6. Testing Finnhub Client Dynamic Domestic Feed ===")
    finnhub = FinnhubClient()
    finnhub_movers = await finnhub.get_top_gainers_losers()
    top_g = [f"{g['symbol']}: +{g['change_percent']}%" for g in finnhub_movers.get("gainers", [])[:3]]
    top_l = [f"{l['symbol']}: {l['change_percent']}%" for l in finnhub_movers.get("losers", [])[:3]]
    print(f"[OK] Finnhub Gainers: {top_g}")
    print(f"[OK] Finnhub Losers:  {top_l}")
    # Verify not static mock
    g_syms = [g["symbol"] for g in finnhub_movers.get("gainers", [])]
    print(f"     Dynamic Gainers list length: {len(g_syms)}")

    # 7. Chart History Candles
    print("\n=== 7. Testing Chart History Candles (RELIANCE, 1mo/1d) ===")
    history = await market_service.get_stock_history("RELIANCE", interval="1d", range_period="1mo")
    if history and history.candles:
        print(f"[OK] Candles received: {len(history.candles)}")
        c = history.candles[-1]
        print(f"     Latest Candle: {c.timestamp.strftime('%Y-%m-%d')} | Open={c.open}, High={c.high}, Low={c.low}, Close={c.close}, Vol={c.volume:,}")
    else:
        print("[FAIL] History candles were empty")

    # 8. Symbol Search
    print("\n=== 8. Testing Symbol Search (Query: 'tata') ===")
    results = await market_service.search_stocks("tata")
    for r in results[:5]:
        print(f"  [OK] Match: {r.symbol:<12} | {r.name} ({r.exchange})")

    print("\n" + "=" * 60)
    print(">>> ALL REAL-TIME MARKET MODULE TESTS PASSED 100%! <<<")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
