"""
FastAPI Dependencies for Market Reports API.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.market_reports.application.queries import (
    GetLatestReportQuery,
    GetReportByIdQuery,
    ListReportsQuery,
)
from app.modules.market_reports.application.use_cases.generate_global_post_market_report import (
    GenerateGlobalPostMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_global_pre_market_report import (
    GenerateGlobalPreMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_post_market_report import (
    GeneratePostMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_pre_market_report import (
    GeneratePreMarketReportUseCase,
)
from app.modules.market_reports.infrastructure.adapters.adr_adapter import IndianADRAdapter
from app.modules.market_reports.infrastructure.adapters.commodities_adapter import CommoditiesAdapter
from app.modules.market_reports.infrastructure.adapters.corporate_announcements_adapter import (
    CorporateAnnouncementsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.currency_adapter import CurrencyAdapter
from app.modules.market_reports.infrastructure.adapters.finnhub_client import (
    FinnhubClient,
)
from app.modules.market_reports.infrastructure.adapters.global_indices_adapter import (
    GlobalIndicesAdapter,
)
from app.modules.market_reports.infrastructure.adapters.news_intelligence_adapter import (
    MarketReportNewsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter import (
    NSEMarketDataProvider,
)
from app.modules.market_reports.infrastructure.adapters.stocknews_client import (
    StockNewsApiClient,
)
from app.modules.market_reports.infrastructure.locks import RedisReportLock
from app.modules.market_reports.infrastructure.repositories.market_report_repository import (
    SQLAlchemyMarketReportRepository,
)


def get_market_report_repository(
    db: AsyncSession = Depends(get_db),
) -> SQLAlchemyMarketReportRepository:
    return SQLAlchemyMarketReportRepository(session=db)


def get_finnhub_client() -> FinnhubClient:
    return FinnhubClient()


def get_stocknews_client() -> StockNewsApiClient:
    return StockNewsApiClient()


def get_global_indices_adapter() -> GlobalIndicesAdapter:
    return GlobalIndicesAdapter()


def get_nse_market_data_adapter() -> NSEMarketDataProvider:
    return NSEMarketDataProvider()


def get_commodities_adapter() -> CommoditiesAdapter:
    return CommoditiesAdapter()


def get_currency_adapter() -> CurrencyAdapter:
    return CurrencyAdapter()


def get_adr_adapter() -> IndianADRAdapter:
    return IndianADRAdapter()


def get_corporate_announcements_adapter() -> CorporateAnnouncementsAdapter:
    return CorporateAnnouncementsAdapter()


def get_market_report_news_adapter() -> MarketReportNewsAdapter:
    return MarketReportNewsAdapter()


def get_report_lock() -> RedisReportLock:
    return RedisReportLock()


def get_generate_pre_market_use_case(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
    finnhub_client: FinnhubClient = Depends(get_finnhub_client),
    stocknews_client: StockNewsApiClient = Depends(get_stocknews_client),
    global_indices_adapter: GlobalIndicesAdapter = Depends(get_global_indices_adapter),
    nse_adapter: NSEMarketDataProvider = Depends(get_nse_market_data_adapter),
    commodities_adapter: CommoditiesAdapter = Depends(get_commodities_adapter),
    currency_adapter: CurrencyAdapter = Depends(get_currency_adapter),
    adr_adapter: IndianADRAdapter = Depends(get_adr_adapter),
    corporate_adapter: CorporateAnnouncementsAdapter = Depends(get_corporate_announcements_adapter),
    news_adapter: MarketReportNewsAdapter = Depends(get_market_report_news_adapter),
    lock_port: RedisReportLock = Depends(get_report_lock),
) -> GeneratePreMarketReportUseCase:
    return GeneratePreMarketReportUseCase(
        repository=repository,
        finnhub_client=finnhub_client,
        stocknews_client=stocknews_client,
        global_indices_adapter=global_indices_adapter,
        nse_adapter=nse_adapter,
        commodities_adapter=commodities_adapter,
        currency_adapter=currency_adapter,
        adr_adapter=adr_adapter,
        corporate_adapter=corporate_adapter,
        news_adapter=news_adapter,
        lock_port=lock_port,
    )


def get_generate_post_market_use_case(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
    finnhub_client: FinnhubClient = Depends(get_finnhub_client),
    stocknews_client: StockNewsApiClient = Depends(get_stocknews_client),
    nse_adapter: NSEMarketDataProvider = Depends(get_nse_market_data_adapter),
    commodities_adapter: CommoditiesAdapter = Depends(get_commodities_adapter),
    corporate_adapter: CorporateAnnouncementsAdapter = Depends(get_corporate_announcements_adapter),
    news_adapter: MarketReportNewsAdapter = Depends(get_market_report_news_adapter),
    lock_port: RedisReportLock = Depends(get_report_lock),
) -> GeneratePostMarketReportUseCase:
    return GeneratePostMarketReportUseCase(
        repository=repository,
        finnhub_client=finnhub_client,
        stocknews_client=stocknews_client,
        nse_adapter=nse_adapter,
        commodities_adapter=commodities_adapter,
        corporate_adapter=corporate_adapter,
        news_adapter=news_adapter,
        lock_port=lock_port,
    )


def get_generate_global_pre_market_use_case(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
    finnhub_client: FinnhubClient = Depends(get_finnhub_client),
    stocknews_client: StockNewsApiClient = Depends(get_stocknews_client),
    lock_port: RedisReportLock = Depends(get_report_lock),
) -> GenerateGlobalPreMarketReportUseCase:
    return GenerateGlobalPreMarketReportUseCase(
        repository=repository,
        finnhub_client=finnhub_client,
        stocknews_client=stocknews_client,
        lock_port=lock_port,
    )


def get_generate_global_post_market_use_case(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
    finnhub_client: FinnhubClient = Depends(get_finnhub_client),
    stocknews_client: StockNewsApiClient = Depends(get_stocknews_client),
    lock_port: RedisReportLock = Depends(get_report_lock),
) -> GenerateGlobalPostMarketReportUseCase:
    return GenerateGlobalPostMarketReportUseCase(
        repository=repository,
        finnhub_client=finnhub_client,
        stocknews_client=stocknews_client,
        lock_port=lock_port,
    )


def get_latest_report_query(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
) -> GetLatestReportQuery:
    return GetLatestReportQuery(repository=repository)


def get_report_by_id_query(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
) -> GetReportByIdQuery:
    return GetReportByIdQuery(repository=repository)


def get_list_reports_query(
    repository: SQLAlchemyMarketReportRepository = Depends(get_market_report_repository),
) -> ListReportsQuery:
    return ListReportsQuery(repository=repository)
