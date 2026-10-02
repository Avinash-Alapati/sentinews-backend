import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import AsyncSessionLocal
from app.modules.market_reports.application.use_cases.generate_post_market_report import (
    GeneratePostMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_pre_market_report import (
    GeneratePreMarketReportUseCase,
)
from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient
from app.modules.market_reports.infrastructure.adapters.stocknews_client import (
    StockNewsApiClient,
)
from app.modules.market_reports.infrastructure.locks import RedisReportLock
from app.modules.market_reports.infrastructure.repositories.market_report_repository import (
    SQLAlchemyMarketReportRepository,
)


async def main():
    print("Generating Pre-Market and Post-Market Reports in PostgreSQL...")
    async with AsyncSessionLocal() as session:
        repo = SQLAlchemyMarketReportRepository(session)
        finnhub = FinnhubClient()
        stocknews = StockNewsApiClient()
        lock = RedisReportLock()

        # 1. Pre-market report
        pre_uc = GeneratePreMarketReportUseCase(repo, finnhub, stocknews, lock)
        pre_report = await pre_uc.execute(force=True)
        print(f"SUCCESS: Pre-Market Report ID={pre_report.id}, Status={pre_report.status}, is_partial={pre_report.is_partial}")

        # 2. Post-market report
        post_uc = GeneratePostMarketReportUseCase(repo, finnhub, stocknews, lock)
        post_report = await post_uc.execute(force=True)
        print(f"SUCCESS: Post-Market Report ID={post_report.id}, Status={post_report.status}, is_partial={post_report.is_partial}")


if __name__ == "__main__":
    asyncio.run(main())
