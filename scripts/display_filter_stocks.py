import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=20.0)

filters = [
    ("NIFTY 50", "nifty50"),
    ("NIFTY 100", "nifty100"),
    ("NIFTY 500", "nifty500"),
    ("NIFTY TOTAL MARKET", "total_market"),
]

for label, f in filters:
    res = client.get(f"/api/v1/market/movers?filter={f}&limit=10")
    print("=" * 80)
    print(f"INDEX FILTER: {label} (API: /api/v1/market/movers?filter={f})")
    print("=" * 80)
    if res.status_code == 200:
        data = res.json()
        print(f"Total Matches -> Gainers: {data['total_gainers']} | Losers: {data['total_losers']} | Most Active: {data['total_most_active']}")

        print("\n[TOP GAINERS]")
        for idx, g in enumerate(data["top_gainers"][:10], 1):
            sym = g["symbol"]
            pct = g["change_percent"]
            price = g["current_price"]
            vol = f"{g['volume']:,}" if g.get("volume") else "N/A"
            name = g["company_name"]
            print(f"  {idx:2d}. {sym:12} {pct:>+6.2f}% | Price: Rs {price:>8.2f} | Traded Vol: {vol:>12} | {name}")

        print("\n[TOP LOSERS]")
        for idx, l in enumerate(data["top_losers"][:10], 1):
            sym = l["symbol"]
            pct = l["change_percent"]
            price = l["current_price"]
            vol = f"{l['volume']:,}" if l.get("volume") else "N/A"
            name = l["company_name"]
            print(f"  {idx:2d}. {sym:12} {pct:>+6.2f}% | Price: Rs {price:>8.2f} | Traded Vol: {vol:>12} | {name}")

        print("\n[MOST ACTIVE BY VOLUME]")
        for idx, a in enumerate(data["most_active"][:5], 1):
            sym = a["symbol"]
            pct = a["change_percent"]
            price = a["current_price"]
            vol = f"{a['volume']:,}" if a.get("volume") else "N/A"
            name = a["company_name"]
            print(f"  {idx:2d}. {sym:12} {pct:>+6.2f}% | Price: Rs {price:>8.2f} | Traded Vol: {vol:>12} | {name}")
    else:
        print("Error:", res.text[:200])
    print("\n")
