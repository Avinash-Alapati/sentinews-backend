import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=20.0)

filters = ["all", "nifty50", "nifty500", "midcap100", "smallcap100", "total_market"]

for f in filters:
    res = client.get(f"/api/v1/market/overview?filter={f}&limit=15")
    print(f"\n==================== Filter: {f} (Status: {res.status_code}) ====================")
    if res.status_code == 200:
        data = res.json()
        gainers = data.get("top_gainers", [])
        losers = data.get("top_losers", [])
        active = data.get("most_active", [])
        print(f"Counts: {len(gainers)} gainers, {len(losers)} losers, {len(active)} most active")
        
        print("--- Top 5 Gainers ---")
        for g in gainers[:5]:
            sym = g["symbol"]
            pct = g["change_percent"]
            price = g["current_price"]
            name = g["company_name"]
            print(f"  {sym:12} {pct:+6.2f}% | Rs {price:8.2f} | {name[:30]}")

        print("--- Top 5 Losers ---")
        for l in losers[:5]:
            sym = l["symbol"]
            pct = l["change_percent"]
            price = l["current_price"]
            name = l["company_name"]
            print(f"  {sym:12} {pct:+6.2f}% | Rs {price:8.2f} | {name[:30]}")

        # Assert monotonicity
        g_pcts = [g["change_percent"] for g in gainers]
        assert g_pcts == sorted(g_pcts, reverse=True), f"Gainers not sorted descending! {g_pcts}"
        l_pcts = [l["change_percent"] for l in losers]
        assert l_pcts == sorted(l_pcts), f"Losers not sorted ascending! {l_pcts}"
        print("  [OK] Monotonic descending gainers & ascending losers verified!")
    else:
        print("Error:", res.text[:200])

# Test /api/v1/market/movers
print("\n==================== Dedicated /api/v1/market/movers ====================")
r_movers = client.get("/api/v1/market/movers?filter=nifty50&limit=10")
print("Status:", r_movers.status_code)
if r_movers.status_code == 200:
    m = r_movers.json()
    print(f"Filter: {m['filter']}, Gainers: {m['total_gainers']}, Losers: {m['total_losers']}, Active: {m['total_most_active']}")
    print("Top gainer:", m["top_gainers"][0]["symbol"], m["top_gainers"][0]["change_percent"])
    print("Top loser:", m["top_losers"][0]["symbol"], m["top_losers"][0]["change_percent"])
    print("  [OK] Dedicated /movers verified!")
