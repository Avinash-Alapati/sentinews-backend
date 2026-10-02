import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath("."))

from app.integrations.market.indian_market_provider import IndianMarketProvider

async def main():
    p = IndianMarketProvider()
    client = await p._get_client()

    urls = {
        "1. Gainers (Variations)": p.NSE_GAINERS_URL,
        "2. Losers (Variations)": p.NSE_LOSERS_URL,
        "3. Volume Gainers": p.NSE_VOLUME_GAINERS_URL,
        "4. Most Active by Volume": p.NSE_MOST_ACTIVE_URL,
        "5. Most Active by Value": p.NSE_MOST_ACTIVE_VALUE_URL,
    }

    print("=" * 80)
    print("LIVE VERIFICATION: TESTING FETCH FROM ALL 5 NSE APIS")
    print("=" * 80)

    for name, url in urls.items():
        t0 = time.time()
        try:
            res = await client.get(url, timeout=8.0)
            elapsed = round((time.time() - t0) * 1000, 2)
            content_type = res.headers.get("content-type", "")
            print(f"\nAPI: {name}")
            print(f"URL: {url}")
            print(f"Status: HTTP {res.status_code} | Latency: {elapsed}ms | Content-Type: {content_type}")

            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    keys = list(data.keys())
                    print(f"Structure: JSON Object with keys -> {keys}")
                    # If variations format
                    if "allSec" in data or "NIFTY" in data:
                        for grp in ["allSec", "SecGtr20", "SecLwr20", "FOSec", "NIFTYNEXT50", "NIFTY", "BANKNIFTY"]:
                            if grp in data:
                                items = data[grp].get("data", []) if isinstance(data[grp], dict) else data[grp]
                                print(f"   * Group [{grp:12}]: {len(items)} items")
                                if items:
                                    top = items[0]
                                    sym = top.get("symbol")
                                    pct = top.get("perChange") or top.get("pChange")
                                    price = top.get("lastPrice") or top.get("ltp")
                                    vol = top.get("trade_quantity") or top.get("totalTradedVolume") or top.get("quantityTraded")
                                    print(f"       Top Item -> Symbol: {sym:12} | %Change: {pct}% | Price: Rs {price} | Vol: {vol}")
                    elif "data" in data:
                        items = data["data"]
                        print(f"   * Total Items: {len(items)}")
                        if items:
                            for idx, item in enumerate(items[:3], 1):
                                sym = item.get("symbol")
                                pct = item.get("pChange") or item.get("perChange")
                                price = item.get("lastPrice") or item.get("ltp")
                                vol = item.get("totalTradedVolume") or item.get("quantityTraded")
                                print(f"       #{idx} -> Symbol: {sym:12} | %Change: {pct}% | Price: Rs {price} | Vol: {vol}")
                elif isinstance(data, list):
                    print(f"   * Total Items (list): {len(data)}")
                print(f"VERDICT: [PASS] Real data successfully fetched from {name}")
            else:
                print(f"VERDICT: [FAIL] HTTP {res.status_code}, Body: {res.text[:120]}")
        except Exception as exc:
            print(f"VERDICT: [ERROR] Exception fetching {name}: {exc}")

    print("\n" + "=" * 80)
    print("TESTING FULL INTEGRATION PIPELINE: _fetch_live_nse_movers()")
    print("=" * 80)
    gainers, losers, most_active = await p._fetch_live_nse_movers(limit=10)

    print(f"\nTop 10 Live Gainers (Strictly Descending):")
    for idx, g in enumerate(gainers, 1):
        print(f"  {idx:2d}. {g.symbol:12} {g.change_percent:+6.2f}% | Price: Rs {g.current_price:8.2f} | Volume: {g.volume} | {g.company_name}")

    print(f"\nTop 10 Live Losers (Strictly Ascending):")
    for idx, l in enumerate(losers, 1):
        print(f"  {idx:2d}. {l.symbol:12} {l.change_percent:+6.2f}% | Price: Rs {l.current_price:8.2f} | Volume: {l.volume} | {l.company_name}")

    print(f"\nTop 10 Most Active (By Traded Volume):")
    for idx, a in enumerate(most_active, 1):
        print(f"  {idx:2d}. {a.symbol:12} {a.change_percent:+6.2f}% | Price: Rs {a.current_price:8.2f} | Volume: {a.volume} | {a.company_name}")

    await p.close()

if __name__ == "__main__":
    asyncio.run(main())
