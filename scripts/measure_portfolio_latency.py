import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from datetime import datetime, timedelta, timezone
import httpx
from app.main import app
from app.db.session import async_session_factory
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.portfolio.domain.entities import Portfolio
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import SQLAlchemyPortfolioRepository
import jwt
from app.core.config import settings

def make_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=60),
    }
    return jwt.encode(payload, key=settings.SECRET_KEY, algorithm=settings.ALGORITHM)

async def run_benchmark():
    # Setup test user and portfolio
    async with async_session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        port_repo = SQLAlchemyPortfolioRepository(session)
        
        user = await user_repo.get_by_email("bench_user@sentinews.in")
        if not user:
            user = await user_repo.create(User(email="bench_user@sentinews.in", full_name="Bench User", hashed_password="pwd", is_active=True))
        
        portfolio = await port_repo.get_portfolio_by_user(user.id)
        if not portfolio:
            portfolio = await port_repo.save_portfolio(Portfolio(user_id=user.id, name="Bench Portfolio"))
        await session.commit()
        user_id = user.id
        port_id = portfolio.id

    token = make_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Measure GET /api/v1/portfolio/user/{user_id}
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/user/{user_id}", headers=headers)
        t_user_port = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/user/{user_id}: {res.status_code} in {t_user_port:.2f}ms")

        # 2. Measure POST /api/v1/portfolio/{port_id}/transactions
        t0 = time.perf_counter()
        res = await client.post(
            f"/api/v1/portfolio/{port_id}/transactions",
            headers=headers,
            json={
                "symbol": "INFY",
                "transaction_type": "BUY",
                "quantity": 10,
                "price": 1600.0,
                "name": "Infosys Ltd",
                "sector": "IT & Software"
            }
        )
        t_post_tx = (time.perf_counter() - t0) * 1000
        print(f"POST /portfolio/{port_id}/transactions: {res.status_code} in {t_post_tx:.2f}ms")

        # 3. Measure GET /api/v1/portfolio/{port_id}/overview
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/{port_id}/overview", headers=headers)
        t_overview = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/{port_id}/overview: {res.status_code} in {t_overview:.2f}ms")

        # 4. Measure GET /api/v1/portfolio/{port_id}/holdings
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/{port_id}/holdings", headers=headers)
        t_holdings = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/{port_id}/holdings: {res.status_code} in {t_holdings:.2f}ms")

        # 5. Measure GET /api/v1/portfolio/{port_id}/transactions
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/{port_id}/transactions", headers=headers)
        t_txs = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/{port_id}/transactions: {res.status_code} in {t_txs:.2f}ms")

        # 6. Measure GET /api/v1/portfolio/{port_id}/allocation
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/{port_id}/allocation", headers=headers)
        t_alloc = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/{port_id}/allocation: {res.status_code} in {t_alloc:.2f}ms")

        # 7. Measure GET /api/v1/portfolio/{port_id}/performance
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/{port_id}/performance", headers=headers)
        t_perf = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/{port_id}/performance: {res.status_code} in {t_perf:.2f}ms")

        # 8. Measure GET /api/v1/portfolio/{port_id}/news-feed
        t0 = time.perf_counter()
        res = await client.get(f"/api/v1/portfolio/{port_id}/news-feed", headers=headers)
        t_news = (time.perf_counter() - t0) * 1000
        print(f"GET /portfolio/{port_id}/news-feed: {res.status_code} in {t_news:.2f}ms")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
