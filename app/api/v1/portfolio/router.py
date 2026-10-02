"""
Portfolio API Router.

Exposes complete production endpoints for:
1. Portfolio lifecycle: Create, Get by ID, Get by User (with auto-create), Delete.
2. Transactions & FIFO cost basis: Record BUY/SELL, List Transactions.
3. Holdings & Valuation: Live holdings, Overview with P&L, Weight Allocations.
4. Returns & Analytics: Money-weighted annualized return (XIRR), Top Gainers & Losers.
5. Hyper-personalized News Intelligence: Chronological live news mentioning portfolio holdings.

Compliance framing:
All news feed endpoints present chronological news that mentions holdings you own.
Sentinews does not assess market impact, predict price movement, or provide investment advice.
Every news response carries the mandatory statutory SEBI disclaimer.
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.auth.dependencies import get_current_active_user
from app.api.v1.portfolio.dependencies import (
    get_create_portfolio_use_case,
    get_personalized_feed_use_case,
    get_portfolio_allocation_use_case,
    get_portfolio_overview_use_case,
    get_portfolio_performance_use_case,
    get_portfolio_repository,
    get_record_transaction_use_case,
)
from app.cache.market_cache import market_cache
from app.modules.auth.domain.entities import User
from app.modules.news_intelligence.application.use_cases.get_personalized_feed import (
    GetPersonalizedFeedUseCase,
)
from app.modules.portfolio.application.ports import PortfolioRepository
from app.modules.portfolio.application.use_cases.create_portfolio import CreatePortfolioUseCase
from app.modules.portfolio.application.use_cases.get_portfolio_allocation import (
    GetPortfolioAllocationUseCase,
)
from app.modules.portfolio.application.use_cases.get_portfolio_overview import (
    GetPortfolioOverviewUseCase,
)
from app.modules.portfolio.application.use_cases.get_portfolio_performance import (
    GetPortfolioPerformanceUseCase,
)
from app.modules.portfolio.application.use_cases.record_transaction import (
    RecordTransactionUseCase,
)
from app.modules.portfolio.domain.entities import Portfolio, TransactionType

router = APIRouter(
    prefix="/portfolio",
    tags=["Portfolio"],
    dependencies=[Depends(get_current_active_user)],
)

# Mandatory SEBI disclaimer string
SEBI_MANDATORY_DISCLAIMER = (
    "For informational purposes only. Not investment advice. "
    "Sentinews is not a SEBI-registered investment adviser or research analyst."
)


def _check_portfolio_access(
    portfolio: Optional[Portfolio],
    current_user: User,
    portfolio_id: int,
) -> Portfolio:
    """Helper to verify portfolio existence and user ownership."""
    if not portfolio or (portfolio.user_id != current_user.id and not current_user.is_superuser):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio with ID {portfolio_id} not found",
        )
    return portfolio


async def _fetch_and_check_portfolio_access(
    portfolio_repo: PortfolioRepository,
    portfolio_id: int,
    current_user: User,
) -> Portfolio:
    """Helper to verify portfolio existence and user ownership using lightweight metadata query where available."""
    cache_key = f"portfolio:meta:{portfolio_id}"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=60)
    if status_val in ("HIT", "STALE") and cached_data:
        p_obj = Portfolio(
            id=cached_data["id"],
            user_id=cached_data["user_id"],
            name=cached_data["name"],
            cash_balance=cached_data.get("cash_balance", 0.0),
            holdings=[],
            transactions=[],
        )
        return _check_portfolio_access(p_obj, current_user, portfolio_id)

    if hasattr(portfolio_repo, "get_portfolio_metadata"):
        portfolio = await portfolio_repo.get_portfolio_metadata(portfolio_id)
    else:
        portfolio = await portfolio_repo.get_portfolio(portfolio_id)
    checked = _check_portfolio_access(portfolio, current_user, portfolio_id)
    if checked:
        await market_cache.set_envelope(
            cache_key,
            {"id": checked.id, "user_id": checked.user_id, "name": checked.name, "cash_balance": checked.cash_balance},
            soft_ttl=60,
            hard_ttl=300,
        )
    return checked


# ==========================================
# Request & Response Schemas
# ==========================================

class CompliantNewsResponseBase(BaseModel):
    """
    Base schema guaranteeing that every news response carries the mandatory SEBI disclaimer.
    """
    disclaimer: str = Field(
        default=SEBI_MANDATORY_DISCLAIMER,
        description="Mandatory statutory regulatory disclaimer",
    )


class CreatePortfolioRequest(BaseModel):
    user_id: Optional[int] = None
    name: str = "My Portfolio"
    cash_balance: float = 0.0


class PortfolioResponse(BaseModel):
    id: Optional[int]
    user_id: int
    name: str
    cash_balance: float


class RecordTransactionRequest(BaseModel):
    symbol: str
    transaction_type: TransactionType
    quantity: float
    price: float
    timestamp: Optional[datetime] = None
    name: Optional[str] = None
    sector: Optional[str] = "General"


class TransactionResponse(BaseModel):
    id: Optional[int]
    portfolio_id: Optional[int]
    symbol: str
    transaction_type: TransactionType
    quantity: float
    price: float
    timestamp: datetime


class HoldingOverviewResponse(BaseModel):
    symbol: str
    name: str
    sector: str
    quantity: float
    avg_buy_price: float
    current_price: float
    total_cost: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    weight_pct: float
    day_change: Optional[float] = 0.0
    day_change_percent: Optional[float] = 0.0


class PortfolioOverviewResponse(BaseModel):
    portfolio_id: int
    name: str
    total_invested_value: float
    total_current_value: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float
    total_realized_pnl: float
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    day_pnl: float = 0.0
    day_pnl_percent: float = 0.0
    total_holdings_count: int = 0
    holdings: List[HoldingOverviewResponse] = Field(default_factory=list)


class HoldingAllocationResponse(BaseModel):
    symbol: str
    name: str
    sector: str
    weight_pct: float
    current_value: float


class SectorAllocationResponse(BaseModel):
    sector: str
    weight_pct: float
    total_value: float


class PortfolioAllocationResponse(BaseModel):
    portfolio_id: int
    holdings: List[HoldingAllocationResponse] = Field(default_factory=list)
    sectors: List[SectorAllocationResponse] = Field(default_factory=list)


class HoldingPerformanceResponse(BaseModel):
    symbol: str
    name: str
    sector: str
    quantity: float
    avg_buy_price: float
    current_price: float
    total_cost: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    weight_pct: float


class PortfolioPerformanceResponse(BaseModel):
    portfolio_id: int
    name: str
    total_invested_value: float
    total_current_value: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float
    total_realized_pnl: float
    total_pnl: float
    xirr_pct: Optional[float] = None
    top_gainer: Optional[HoldingPerformanceResponse] = None
    top_loser: Optional[HoldingPerformanceResponse] = None
    holdings: List[HoldingPerformanceResponse] = Field(default_factory=list)


class NewsArticleResponse(CompliantNewsResponseBase):
    """
    SEBI-compliant article metadata representation.
    """
    id: Optional[int] = None
    title: str
    summary: str
    url: str
    source: str
    symbols: List[str] = Field(default_factory=list)
    sectors: List[str] = Field(default_factory=list)
    article_tone: str = "neutral"
    market_context: str = "market_hours"
    sentiment_score: float = 0.0
    sentiment_magnitude: float = 1.0
    is_trending: bool = False
    published_at: datetime
    expires_at: Optional[datetime] = None


class FeedItemResponse(CompliantNewsResponseBase):
    """
    Response item for news that mentions holdings owned in the user's portfolio.
    """
    article: NewsArticleResponse
    relevance_score: float
    matched_holdings: List[str] = Field(default_factory=list)
    is_concentrated_holding_match: bool = False
    notification_triggered: bool = False


class PersonalizedFeedResponse(CompliantNewsResponseBase):
    """
    Complete ranked feed of news that mentions holdings owned in the user's portfolio.
    """
    portfolio_id: int
    generated_at: datetime
    total_count: int
    items: List[FeedItemResponse]


# ==========================================
# Portfolio CRUD Endpoints
# ==========================================

@router.post(
    "",
    response_model=PortfolioResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Portfolio",
)
async def create_portfolio(
    request: CreatePortfolioRequest,
    current_user: User = Depends(get_current_active_user),
    use_case: CreatePortfolioUseCase = Depends(get_create_portfolio_use_case),
) -> PortfolioResponse:
    target_user_id = current_user.id
    if request.user_id is not None and current_user.is_superuser:
        target_user_id = request.user_id

    portfolio = await use_case.execute(user_id=target_user_id, name=request.name)
    await market_cache.delete_pattern("portfolio:*")
    return PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        cash_balance=portfolio.cash_balance,
    )


@router.get(
    "/{id}",
    response_model=PortfolioResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Portfolio Details by ID",
)
async def get_portfolio_by_id(
    id: int,
    metadata_only: bool = Query(
        default=True,
        description="If True, loads portfolio metadata without eager loading child holdings and transactions",
    ),
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
) -> PortfolioResponse:
    cache_key = f"portfolio:meta:{id}"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=60)
    if status_val in ("HIT", "STALE") and cached_data:
        res = PortfolioResponse.model_validate(cached_data)
        if res.user_id == current_user.id or current_user.is_superuser:
            return res

    if metadata_only and hasattr(portfolio_repo, "get_portfolio_metadata"):
        portfolio = await portfolio_repo.get_portfolio_metadata(id)
    else:
        portfolio = await portfolio_repo.get_portfolio(id)
    portfolio = _check_portfolio_access(portfolio, current_user, id)
    resp = PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        cash_balance=portfolio.cash_balance,
    )
    await market_cache.set_envelope(cache_key, resp.model_dump(), soft_ttl=60, hard_ttl=300)
    return resp


@router.get(
    "/user/{user_id}",
    response_model=PortfolioResponse,
    status_code=status.HTTP_200_OK,
    summary="Get or Create Primary Portfolio for User",
)
async def get_portfolio_by_user_id(
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
) -> PortfolioResponse:
    if user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio for user {user_id} not found",
        )
    
    cache_key = f"portfolio:user_meta:{user_id}"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=60)
    if status_val in ("HIT", "STALE") and cached_data:
        return PortfolioResponse.model_validate(cached_data)

    if hasattr(portfolio_repo, "get_portfolio_by_user_metadata"):
        portfolio = await portfolio_repo.get_portfolio_by_user_metadata(user_id)
    else:
        portfolio = await portfolio_repo.get_portfolio_by_user(user_id)
    if not portfolio:
        # Auto-create default portfolio for user
        portfolio = await portfolio_repo.save_portfolio(
            Portfolio(user_id=user_id, name="My Investment Portfolio")
        )
    
    resp = PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        cash_balance=portfolio.cash_balance,
    )
    await market_cache.set_envelope(cache_key, resp.model_dump(), soft_ttl=60, hard_ttl=300)
    return resp


@router.delete(
    "/{id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Portfolio",
)
async def delete_portfolio(
    id: int,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
):
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)
    deleted = await portfolio_repo.delete_portfolio(id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio with ID {id} not found",
        )
    await market_cache.delete_pattern(f"portfolio:{id}:*")
    await market_cache.delete_pattern("portfolio:user_meta:*")
    return {"message": f"Portfolio {id} deleted successfully", "id": id}


# ==========================================
# Transaction Endpoints
# ==========================================

@router.post(
    "/{id}/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record Buy/Sell Transaction with FIFO Cost Basis",
)
async def record_transaction(
    id: int,
    request: RecordTransactionRequest,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
    use_case: RecordTransactionUseCase = Depends(get_record_transaction_use_case),
) -> TransactionResponse:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)
    tx = await use_case.execute(
        portfolio_id=id,
        symbol=request.symbol,
        transaction_type=request.transaction_type,
        quantity=request.quantity,
        price=request.price,
        timestamp=request.timestamp,
        name=request.name or "",
        sector=request.sector or "General",
    )
    # Instantly invalidate all caches for this portfolio
    await market_cache.delete_pattern(f"portfolio:{id}:*")
    await market_cache.delete_pattern("portfolio:user_meta:*")

    return TransactionResponse(
        id=tx.id,
        portfolio_id=tx.portfolio_id,
        symbol=tx.symbol,
        transaction_type=tx.transaction_type,
        quantity=tx.quantity,
        price=tx.price,
        timestamp=tx.timestamp,
    )


@router.get(
    "/{id}/transactions",
    response_model=List[TransactionResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Transaction History for Portfolio",
)
async def get_portfolio_transactions(
    id: int,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page (max 100)"),
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
) -> List[TransactionResponse]:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)
    
    cache_key = f"portfolio:{id}:txs:{page}:{limit}"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=30)
    if status_val in ("HIT", "STALE") and cached_data is not None:
        return [TransactionResponse.model_validate(x) for x in cached_data]

    offset = (page - 1) * limit
    txs = await portfolio_repo.get_transactions(id, limit=limit, offset=offset)
    res = [
        TransactionResponse(
            id=t.id,
            portfolio_id=t.portfolio_id,
            symbol=t.symbol,
            transaction_type=t.transaction_type,
            quantity=t.quantity,
            price=t.price,
            timestamp=t.timestamp,
        )
        for t in txs
    ]
    await market_cache.set_envelope(cache_key, [r.model_dump() for r in res], soft_ttl=30, hard_ttl=120)
    return res


# ==========================================
# Valuation, Holdings, & Performance Endpoints
# ==========================================

@router.get(
    "/{id}/holdings",
    response_model=List[HoldingOverviewResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Active Holdings with Live Market Quotes",
)
async def get_portfolio_holdings(
    id: int,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
    use_case: GetPortfolioOverviewUseCase = Depends(get_portfolio_overview_use_case),
) -> List[HoldingOverviewResponse]:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)

    cache_key = f"portfolio:{id}:holdings"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=30)
    if status_val in ("HIT", "STALE") and cached_data is not None:
        return [HoldingOverviewResponse.model_validate(x) for x in cached_data]

    overview = await use_case.execute(portfolio_id=id)
    if not overview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio with ID {id} not found",
        )
    res = [
        HoldingOverviewResponse(
            symbol=h.symbol,
            name=h.name,
            sector=h.sector,
            quantity=h.quantity,
            avg_buy_price=h.avg_buy_price,
            current_price=h.current_price,
            total_cost=h.total_cost,
            current_value=h.current_value,
            unrealized_pnl=h.unrealized_pnl,
            unrealized_pnl_pct=h.unrealized_pnl_pct,
            weight_pct=h.weight_pct,
            day_change=h.day_change,
            day_change_percent=h.day_change_percent,
        )
        for h in overview.holdings
    ]
    await market_cache.set_envelope(cache_key, [r.model_dump() for r in res], soft_ttl=30, hard_ttl=120)
    return res


@router.get(
    "/{id}/overview",
    response_model=PortfolioOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Portfolio Overview with Realized/Unrealized P&L and Market Prices",
)
async def get_portfolio_overview(
    id: int,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
    use_case: GetPortfolioOverviewUseCase = Depends(get_portfolio_overview_use_case),
) -> PortfolioOverviewResponse:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)

    cache_key = f"portfolio:{id}:overview"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=30)
    if status_val in ("HIT", "STALE") and cached_data is not None:
        return PortfolioOverviewResponse.model_validate(cached_data)

    overview = await use_case.execute(portfolio_id=id)
    if not overview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio with ID {id} not found",
        )
    res = PortfolioOverviewResponse(
        portfolio_id=overview.portfolio_id,
        name=overview.name,
        total_invested_value=overview.total_invested_value,
        total_current_value=overview.total_current_value,
        total_unrealized_pnl=overview.total_unrealized_pnl,
        total_unrealized_pnl_pct=overview.total_unrealized_pnl_pct,
        total_realized_pnl=overview.total_realized_pnl,
        total_pnl=overview.total_pnl,
        total_pnl_pct=overview.total_pnl_pct,
        day_pnl=overview.day_pnl,
        day_pnl_percent=overview.day_pnl_percent,
        total_holdings_count=overview.total_holdings_count,
        holdings=[
            HoldingOverviewResponse(
                symbol=h.symbol,
                name=h.name,
                sector=h.sector,
                quantity=h.quantity,
                avg_buy_price=h.avg_buy_price,
                current_price=h.current_price,
                total_cost=h.total_cost,
                current_value=h.current_value,
                unrealized_pnl=h.unrealized_pnl,
                unrealized_pnl_pct=h.unrealized_pnl_pct,
                weight_pct=h.weight_pct,
                day_change=h.day_change,
                day_change_percent=h.day_change_percent,
            )
            for h in overview.holdings
        ],
    )
    await market_cache.set_envelope(cache_key, res.model_dump(), soft_ttl=30, hard_ttl=120)
    return res


@router.get(
    "/{id}/allocation",
    response_model=PortfolioAllocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Portfolio Holdings and Sector Allocation Weights",
)
async def get_portfolio_allocation(
    id: int,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
    use_case: GetPortfolioAllocationUseCase = Depends(get_portfolio_allocation_use_case),
) -> PortfolioAllocationResponse:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)

    cache_key = f"portfolio:{id}:allocation"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=30)
    if status_val in ("HIT", "STALE") and cached_data is not None:
        return PortfolioAllocationResponse.model_validate(cached_data)

    alloc = await use_case.execute(portfolio_id=id)
    if not alloc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio with ID {id} not found",
        )
    res = PortfolioAllocationResponse(
        portfolio_id=alloc.portfolio_id,
        holdings=[
            HoldingAllocationResponse(
                symbol=h.symbol,
                name=h.name,
                sector=h.sector,
                weight_pct=h.weight_pct,
                current_value=h.current_value,
            )
            for h in alloc.holdings
        ],
        sectors=[
            SectorAllocationResponse(
                sector=s.sector,
                weight_pct=s.weight_pct,
                total_value=s.total_value,
            )
            for s in alloc.sectors
        ],
    )
    await market_cache.set_envelope(cache_key, res.model_dump(), soft_ttl=30, hard_ttl=120)
    return res


@router.get(
    "/{id}/performance",
    response_model=PortfolioPerformanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Annualized Money-Weighted Returns (XIRR) and Top Gainers/Losers",
)
async def get_portfolio_performance(
    id: int,
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
    use_case: GetPortfolioPerformanceUseCase = Depends(get_portfolio_performance_use_case),
) -> PortfolioPerformanceResponse:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)

    cache_key = f"portfolio:{id}:performance"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=30)
    if status_val in ("HIT", "STALE") and cached_data is not None:
        return PortfolioPerformanceResponse.model_validate(cached_data)

    perf = await use_case.execute(portfolio_id=id)
    if not perf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio with ID {id} not found",
        )
    
    def _map_holding(h):
        if not h:
            return None
        return HoldingPerformanceResponse(
            symbol=h.symbol,
            name=h.name,
            sector=h.sector,
            quantity=h.quantity,
            avg_buy_price=h.avg_buy_price,
            current_price=h.current_price,
            total_cost=h.total_cost,
            current_value=h.current_value,
            unrealized_pnl=h.unrealized_pnl,
            unrealized_pnl_pct=h.unrealized_pnl_pct,
            weight_pct=h.weight_pct,
        )

    res = PortfolioPerformanceResponse(
        portfolio_id=perf.portfolio_id,
        name=perf.name,
        total_invested_value=perf.total_invested_value,
        total_current_value=perf.total_current_value,
        total_unrealized_pnl=perf.total_unrealized_pnl,
        total_unrealized_pnl_pct=perf.total_unrealized_pnl_pct,
        total_realized_pnl=perf.total_realized_pnl,
        total_pnl=perf.total_pnl,
        xirr_pct=perf.xirr_pct,
        top_gainer=_map_holding(perf.top_gainer),
        top_loser=_map_holding(perf.top_loser),
        holdings=[_map_holding(h) for h in perf.holdings],
    )
    await market_cache.set_envelope(cache_key, res.model_dump(), soft_ttl=30, hard_ttl=120)
    return res


# ==========================================
# Personalized News Feed Endpoint
# ==========================================

@router.get(
    "/{id}/news-feed",
    response_model=PersonalizedFeedResponse,
    summary="Get Hyper-Personalized News Feed Mentioning Portfolio Holdings",
    description=(
        "Retrieves a ranked news feed mentioning holdings owned in the portfolio from live RSS feeds. "
        "Applies semantic vector similarity, exponential recency decay, article tone magnitude, "
        "trending boost, and concentration-aware floor filtering. Not investment advice."
    ),
    status_code=status.HTTP_200_OK,
)
async def get_portfolio_news_feed(
    id: int,
    limit: int = Query(20, ge=1, le=100, description="Max number of feed items"),
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0, description="Optional minimum relevance score cutoff"),
    current_user: User = Depends(get_current_active_user),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
    use_case: GetPersonalizedFeedUseCase = Depends(get_personalized_feed_use_case),
) -> PersonalizedFeedResponse:
    await _fetch_and_check_portfolio_access(portfolio_repo, id, current_user)

    cache_key = f"portfolio:{id}:news:{limit}:{min_score}"
    cached_data, status_val, _ = await market_cache.get_envelope(cache_key, soft_ttl_override=60)
    if status_val in ("HIT", "STALE") and cached_data is not None:
        return PersonalizedFeedResponse.model_validate(cached_data)

    feed = await use_case.execute(
        portfolio_id=id,
        limit=limit,
        min_score=min_score,
    )

    items_response = [
        FeedItemResponse(
            article=NewsArticleResponse(
                id=item.article.id,
                title=item.article.title,
                summary=item.article.summary,
                url=item.article.url,
                source=item.article.source,
                symbols=item.article.symbols,
                sectors=item.article.sectors,
                article_tone=item.article.article_tone,
                market_context=item.article.market_context,
                sentiment_score=item.article.sentiment_score,
                sentiment_magnitude=item.article.sentiment_magnitude,
                is_trending=item.article.is_trending,
                published_at=item.article.published_at,
                expires_at=item.article.expires_at,
            ),
            relevance_score=item.relevance_score,
            matched_holdings=item.matched_holdings,
            is_concentrated_holding_match=item.is_concentrated_holding_match,
            notification_triggered=item.notification_triggered,
        )
        for item in feed.items
    ]

    res = PersonalizedFeedResponse(
        portfolio_id=feed.portfolio_id,
        generated_at=feed.generated_at,
        total_count=feed.total_count,
        items=items_response,
    )
    await market_cache.set_envelope(cache_key, res.model_dump(), soft_ttl=60, hard_ttl=300)
    return res
