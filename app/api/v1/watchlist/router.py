"""
Watchlist API Router.

Endpoints for managing personal stock collections (Watchlists):
1. Create watchlist
2. List authenticated user's watchlists
3. Get single watchlist with stocks
4. Update / rename watchlist
5. Delete watchlist
6. Add stock to watchlist (preventing duplicates)
7. Remove stock from watchlist
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.api.v1.auth.dependencies import get_current_active_user
from app.api.v1.watchlist.dependencies import (
    get_add_stock_to_watchlist_use_case,
    get_create_watchlist_use_case,
    get_delete_watchlist_use_case,
    get_get_watchlist_use_case,
    get_get_watchlists_use_case,
    get_remove_stock_from_watchlist_use_case,
    get_update_watchlist_use_case,
)
from app.modules.auth.domain.entities import User
from app.modules.watchlist.application.ports import (
    DuplicateStockError,
    DuplicateWatchlistError,
    InvalidWatchlistDataError,
    StockNotFoundError,
    WatchlistNotFoundError,
)
from app.modules.watchlist.application.use_cases.add_stock_to_watchlist import (
    AddStockToWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.create_watchlist import (
    CreateWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.delete_watchlist import (
    DeleteWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.get_watchlist import (
    GetWatchlistUseCase,
)

from app.modules.watchlist.application.use_cases.get_watchlists import (
    GetWatchlistsUseCase,
)
from app.modules.watchlist.application.use_cases.remove_stock_from_watchlist import (
    RemoveStockFromWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.update_watchlist import (
    UpdateWatchlistUseCase,
)

router = APIRouter(prefix="/watchlists", tags=["Watchlist"])


# ==========================================
# Request & Response Schemas
# ==========================================


class CreateWatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Name of the watchlist", example="IT Companies")


class UpdateWatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="New name for the watchlist", example="Indian IT Companies")


class AddStockRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=50, description="Stock ticker symbol (e.g. TCS, INFY, RELIANCE)", example="TCS")
    exchange: Optional[str] = Field("NSE", max_length=20, description="Stock exchange name", example="NSE")
    notes: Optional[str] = Field(None, max_length=255, description="Optional personal research notes", example="Tracking for Q2 results")


class WatchlistItemResponse(BaseModel):
    id: int
    watchlist_id: int
    symbol: str
    exchange: str
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WatchlistSummaryResponse(BaseModel):
    id: int
    name: str
    stock_count: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WatchlistDetailResponse(BaseModel):
    id: int
    name: str
    user_id: int
    stock_count: int
    stocks: List[WatchlistItemResponse]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MessageResponse(BaseModel):
    message: str
    success: bool = True


def _to_item_response(item) -> WatchlistItemResponse:
    return WatchlistItemResponse(
        id=item.id,
        watchlist_id=item.watchlist_id,
        symbol=item.symbol,
        exchange=item.exchange,
        notes=item.notes,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_detail_response(watchlist) -> WatchlistDetailResponse:
    return WatchlistDetailResponse(
        id=watchlist.id,
        name=watchlist.name,
        user_id=watchlist.user_id,
        stock_count=watchlist.stock_count,
        stocks=[_to_item_response(i) for i in watchlist.items],
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
    )


def _to_summary_response(watchlist) -> WatchlistSummaryResponse:
    return WatchlistSummaryResponse(
        id=watchlist.id,
        name=watchlist.name,
        stock_count=watchlist.stock_count,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
    )


# ==========================================
# Endpoints
# ==========================================


@router.get(
    "",
    response_model=List[WatchlistDetailResponse],
    summary="Get Authenticated User's Watchlists",
    description="Returns all watchlists created by the currently authenticated user, including stock items.",
)
async def get_user_watchlists(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page (max 100)"),
    current_user: User = Depends(get_current_active_user),
    use_case: GetWatchlistsUseCase = Depends(get_get_watchlists_use_case),
) -> List[WatchlistDetailResponse]:
    watchlists = await use_case.execute(user_id=current_user.id)
    offset = (page - 1) * limit
    sliced = watchlists[offset : offset + limit] if watchlists else []
    return [_to_detail_response(w) for w in sliced]


@router.post(
    "",
    response_model=WatchlistDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Watchlist",
    description="Creates a new empty watchlist for the authenticated user.",
)
async def create_watchlist(
    request: CreateWatchlistRequest,
    current_user: User = Depends(get_current_active_user),
    use_case: CreateWatchlistUseCase = Depends(get_create_watchlist_use_case),
) -> WatchlistDetailResponse:
    try:
        watchlist = await use_case.execute(
            user_id=current_user.id,
            name=request.name,
        )
        return _to_detail_response(watchlist)
    except DuplicateWatchlistError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except InvalidWatchlistDataError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/user/{user_id}",
    response_model=List[WatchlistSummaryResponse],
    summary="Get User's Watchlists",
    description="Get all watchlists belonging to the specified authenticated user. Returns summary information without stock details.",
)
async def get_user_watchlists_by_id(
    user_id: int = Path(..., description="ID of the authenticated user"),
    current_user: User = Depends(get_current_active_user),
    use_case: GetWatchlistsUseCase = Depends(get_get_watchlists_use_case),
) -> List[WatchlistSummaryResponse]:
    if user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlists for user {user_id} not found",
        )
    watchlists = await use_case.execute(user_id=current_user.id, summary=True)
    return [_to_summary_response(w) for w in watchlists]


@router.get(
    "/{watchlist_id}",
    response_model=WatchlistDetailResponse,
    summary="Get Single Watchlist",
    description="Retrieve a single watchlist by ID including its stock items. Must belong to the authenticated user.",
)
async def get_watchlist(
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    current_user: User = Depends(get_current_active_user),
    use_case: GetWatchlistUseCase = Depends(get_get_watchlist_use_case),
) -> WatchlistDetailResponse:
    try:
        watchlist = await use_case.execute(
            user_id=current_user.id,
            watchlist_id=watchlist_id,
        )
        return _to_detail_response(watchlist)
    except WatchlistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist with id {watchlist_id} not found",
        )


@router.get(
    "/{watchlist_id}/stocks",
    response_model=List[WatchlistItemResponse],
    summary="Get Watchlist Stocks",
    description="Retrieve all stock items belonging to a specific watchlist owned by the authenticated user.",
)
async def get_watchlist_stocks(
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    current_user: User = Depends(get_current_active_user),
    use_case: GetWatchlistUseCase = Depends(get_get_watchlist_use_case),
) -> List[WatchlistItemResponse]:
    try:
        watchlist = await use_case.execute(
            user_id=current_user.id,
            watchlist_id=watchlist_id,
        )
        return [_to_item_response(i) for i in watchlist.items]
    except WatchlistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist with id {watchlist_id} not found",
        )


@router.patch(
    "/{watchlist_id}",
    response_model=WatchlistDetailResponse,
    summary="Rename/Update Watchlist",
    description="Renames an existing watchlist belonging to the authenticated user.",
)
async def update_watchlist(
    request: UpdateWatchlistRequest,
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    current_user: User = Depends(get_current_active_user),
    use_case: UpdateWatchlistUseCase = Depends(get_update_watchlist_use_case),
) -> WatchlistDetailResponse:
    try:
        updated = await use_case.execute(
            user_id=current_user.id,
            watchlist_id=watchlist_id,
            name=request.name,
        )
        return _to_detail_response(updated)
    except WatchlistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist with id {watchlist_id} not found",
        )
    except DuplicateWatchlistError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except InvalidWatchlistDataError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{watchlist_id}",
    response_model=WatchlistDetailResponse,
    summary="Update Watchlist (PUT)",
    description="Updates an existing watchlist name.",
)
async def update_watchlist_put(
    request: UpdateWatchlistRequest,
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    current_user: User = Depends(get_current_active_user),
    use_case: UpdateWatchlistUseCase = Depends(get_update_watchlist_use_case),
) -> WatchlistDetailResponse:
    return await update_watchlist(
        request=request,
        watchlist_id=watchlist_id,
        current_user=current_user,
        use_case=use_case,
    )


@router.delete(
    "/{watchlist_id}",
    response_model=MessageResponse,
    summary="Delete Watchlist",
    description="Permanently deletes a watchlist and all contained stock items.",
)
async def delete_watchlist(
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    current_user: User = Depends(get_current_active_user),
    use_case: DeleteWatchlistUseCase = Depends(get_delete_watchlist_use_case),
) -> MessageResponse:
    try:
        await use_case.execute(
            user_id=current_user.id,
            watchlist_id=watchlist_id,
        )
        return MessageResponse(message=f"Watchlist {watchlist_id} deleted successfully")
    except WatchlistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist with id {watchlist_id} not found",
        )


@router.post(
    "/{watchlist_id}/stocks",
    response_model=WatchlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Stock to Watchlist",
    description="Adds a stock ticker symbol to a user's watchlist. Prevents duplicate stocks inside the same watchlist.",
)
async def add_stock_to_watchlist(
    request: AddStockRequest,
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    current_user: User = Depends(get_current_active_user),
    use_case: AddStockToWatchlistUseCase = Depends(get_add_stock_to_watchlist_use_case),
) -> WatchlistItemResponse:
    try:
        item = await use_case.execute(
            user_id=current_user.id,
            watchlist_id=watchlist_id,
            symbol=request.symbol,
            exchange=request.exchange or "NSE",
            notes=request.notes,
        )
        return _to_item_response(item)
    except WatchlistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist with id {watchlist_id} not found",
        )
    except DuplicateStockError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except InvalidWatchlistDataError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete(
    "/{watchlist_id}/stocks/{symbol_or_id}",
    response_model=MessageResponse,
    summary="Remove Stock from Watchlist",
    description="Removes a stock item from a watchlist by symbol (e.g. TCS) or item ID.",
)
async def remove_stock_from_watchlist(
    watchlist_id: int = Path(..., description="ID of the watchlist"),
    symbol_or_id: str = Path(..., description="Stock symbol (e.g. TCS) or item ID"),
    current_user: User = Depends(get_current_active_user),
    use_case: RemoveStockFromWatchlistUseCase = Depends(get_remove_stock_from_watchlist_use_case),
) -> MessageResponse:
    try:
        await use_case.execute(
            user_id=current_user.id,
            watchlist_id=watchlist_id,
            symbol_or_item_id=symbol_or_id,
        )
        return MessageResponse(
            message=f"Stock '{symbol_or_id}' removed from watchlist {watchlist_id}"
        )
    except WatchlistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist with id {watchlist_id} not found",
        )
    except StockNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock '{symbol_or_id}' not found in watchlist {watchlist_id}",
        )
