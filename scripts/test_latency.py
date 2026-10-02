import asyncio
import os
import sys
import time
import httpx

sys.path.insert(0, os.path.abspath("."))

from app.core.config import settings
from app.modules.auth.domain.services.security import create_access_token

async def run_benchmark():
    # Generate valid token for user 1 (investor@sentinews.in)
    token = create_access_token(
        subject="1",
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        extra_claims={"email": "investor@sentinews.in"}
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30.0) as client:
        print("\n--- 1. GET /api/v1/portfolio/user/1 ---")
        t0 = time.time()
        res = await client.get("/api/v1/portfolio/user/1", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")
        if res.status_code != 200:
            print("Error response text:", res.text)
        p_data = res.json()
        print("Data:", p_data)
        p_id = p_data.get("id")

        if not p_id:
            print("ERROR: No portfolio ID found.")
            return

        print(f"\n--- 2. GET /api/v1/portfolio/{p_id}/overview ---")
        t0 = time.time()
        res = await client.get(f"/api/v1/portfolio/{p_id}/overview", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")
        print("Overview:", res.json())

        print(f"\n--- 3. GET /api/v1/portfolio/{p_id}/holdings ---")
        t0 = time.time()
        res = await client.get(f"/api/v1/portfolio/{p_id}/holdings", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")
        print("Holdings Count:", len(res.json()))

        print(f"\n--- 4. GET /api/v1/portfolio/{p_id}/transactions ---")
        t0 = time.time()
        res = await client.get(f"/api/v1/portfolio/{p_id}/transactions", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")
        print("Transactions Count:", len(res.json()))

        print(f"\n--- 5. GET /api/v1/portfolio/{p_id}/allocation ---")
        t0 = time.time()
        res = await client.get(f"/api/v1/portfolio/{p_id}/allocation", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")

        print(f"\n--- 6. GET /api/v1/portfolio/{p_id}/performance ---")
        t0 = time.time()
        res = await client.get(f"/api/v1/portfolio/{p_id}/performance", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")

        print(f"\n--- 7. POST /api/v1/portfolio/{p_id}/transactions (RECORD BUY) ---")
        t0 = time.time()
        res = await client.post(
            f"/api/v1/portfolio/{p_id}/transactions",
            json={
                "symbol": "RELIANCE",
                "transaction_type": "BUY",
                "quantity": 10,
                "price": 2900.0,
                "name": "Reliance Industries Ltd",
                "sector": "Energy & Oil",
            },
            headers=headers,
        )
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")
        print("Recorded Tx:", res.json())

        print(f"\n--- 8. GET /api/v1/portfolio/{p_id}/news-feed ---")
        t0 = time.time()
        res = await client.get(f"/api/v1/portfolio/{p_id}/news-feed", headers=headers)
        print(f"Status: {res.status_code}, Time: {round((time.time()-t0)*1000, 1)}ms")
        print("Feed items count:", len(res.json().get("items", [])))

if __name__ == "__main__":
    asyncio.run(run_benchmark())
