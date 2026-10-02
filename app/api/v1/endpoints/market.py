from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Response

from app.modules.market_intelligence.application.service import market_service
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    StockHistoryResponse,
    StockSearchResult,
    ETFQuote,
    ETFListResponse,
)

router = APIRouter(prefix="/market", tags=["Market Intelligence"])


@router.get(
    "/indices",
    response_model=List[IndexQuote],
    summary="Get Real-Time Indian Indices (NIFTY 50, SENSEX, BANK NIFTY, NIFTY IT)",
)
async def get_indian_indices(response: Response):
    """
    Fetch benchmark quotes for major Indian benchmark indices from cache.
    Injects X-Cache (HIT|STALE|MISS) and X-Data-Age headers.
    """
    indices, status, age = await market_service.get_indian_indices()
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"
    return indices


@router.get(
    "/overview",
    response_model=MarketOverview,
    summary="Get Live Indian Market Overview (Status, Indices, Top Gainers & Losers)",
)
async def get_market_overview(
    response: Response,
    filter: Optional[str] = Query(
        "all",
        description="Filter movers by index: all, nifty50, nifty500, midcap100, smallcap100, total_market",
    ),
    limit: int = Query(
        20,
        ge=5,
        le=100,
        description="Number of gainers/losers/most active to return",
    ),
):
    """
    Returns high-level snapshot of the Indian market from cache:
    - Real-time Market Status (OPEN / CLOSED)
    - Major benchmark indices (NIFTY 50, SENSEX, NIFTY BANK, etc.)
    - Top gainers and top losers filtered by index
    - Most active volume movers
    """
    overview, status, age = await market_service.get_market_overview(index_filter=filter, limit=limit)
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"
    return overview


@router.get(
    "/movers",
    summary="Get Real-Time Indian Stock Market Top Gainers and Losers",
)
async def get_market_movers(
    response: Response,
    filter: Optional[str] = Query(
        "all",
        description="Filter by index: all, nifty50, nifty500, midcap100, smallcap100, total_market",
    ),
    limit: int = Query(
        20,
        ge=5,
        le=100,
        description="Number of gainers and losers to return",
    ),
):
    """
    Get top gainers, losers, and most active stocks across the broad Indian stock market.
    """
    overview, status, age = await market_service.get_market_overview(index_filter=filter, limit=limit)
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"
    return {
        "filter": filter,
        "total_gainers": len(overview.top_gainers),
        "total_losers": len(overview.top_losers),
        "total_most_active": len(overview.most_active),
        "top_gainers": overview.top_gainers,
        "top_losers": overview.top_losers,
        "most_active": overview.most_active,
    }


from app.modules.market_intelligence.domain.symbol_master import symbol_master


@router.get(
    "/quote/{symbol}",
    response_model=StockQuote,
    summary="Get Real-Time Stock Quote for NSE / BSE Symbol",
)
async def get_stock_quote(symbol: str, response: Response):
    """
    Fetch live dynamic quote for any Indian stock from cache.
    Returns 503 with Retry-After if valid symbol is warming in cache, or 404 for unknown symbols.
    """
    quote, status, age = await market_service.get_realtime_quote(symbol)
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"

    if not quote:
        clean_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        if symbol_master.get_symbol(clean_sym) is not None:
            raise HTTPException(
                status_code=503,
                detail=f"Quote for symbol '{symbol}' is warming up in cache. Please retry in a moment.",
                headers={"X-Cache": status, "X-Data-Age": f"{age:.2f}s", "Retry-After": "2"},
            )
        raise HTTPException(
            status_code=404,
            detail=f"Real-time quote for symbol '{symbol}' could not be found or fetched",
            headers={"X-Cache": status, "X-Data-Age": f"{age:.2f}s"},
        )
    return quote


@router.get(
    "/quotes",
    response_model=List[StockQuote],
    summary="Get Batch Real-Time Stock Quotes",
)
async def get_stock_quotes(
    response: Response,
    symbols: str = Query(
        ...,
        description="Comma-separated list of stock symbols, e.g. 'RELIANCE,TCS,INFY,HDFCBANK'",
    ),
):
    """
    Fetch live dynamic quotes for multiple Indian stocks in a single request from cache.
    """
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=400, detail="Please provide at least one symbol")

    quotes, status, age = await market_service.get_realtime_quotes(sym_list)
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"
    return quotes


@router.get(
    "/history/{symbol}",
    response_model=StockHistoryResponse,
    summary="Get Historical & Intraday Candle Data for Charts",
)
async def get_stock_history(
    symbol: str,
    response: Response,
    interval: str = Query("1d", description="Candle interval: 1m, 5m, 15m, 1h, 1d, 1wk"),
    range_period: Optional[str] = Query(None, description="Time range: 1d, 5d, 1mo, 3mo, 6mo, 1y, 5y, max"),
    range: Optional[str] = Query(None, description="Alias for range_period"),
):
    """
    Fetch OHLCV candles for charting from cache.
    """
    selected_range = range or range_period or "1mo"
    history, status, age = await market_service.get_stock_history(
        symbol, interval=interval, range_period=selected_range
    )
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"

    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"Historical data for symbol '{symbol}' not found or currently warming up",
            headers={"X-Cache": status, "X-Data-Age": f"{age:.2f}s"},
        )
    return history


@router.get(
    "/search",
    response_model=List[StockSearchResult],
    summary="Search Indian Stocks (NSE / BSE)",
)
async def search_stocks(response: Response, query: str = Query(..., min_length=1)):
    """
    Search Indian stocks locally against the In-Memory Symbol Master.
    Zero upstream network delay.
    """
    results, status, age = await market_service.search_stocks(query)
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"
    return results


@router.get(
    "/etfs",
    response_model=ETFListResponse,
    summary="Get Real-Time Indian ETFs (NSE & BSE)",
)
async def get_etfs(
    response: Response,
    category: Optional[str] = Query(
        None,
        description="Filter by category (e.g. 'gold', 'silver', 'debt', 'sectoral', 'broad_market', 'smart_beta', 'global')",
    ),
    search: Optional[str] = Query(
        None,
        description="Search by symbol or underlying asset name (e.g. 'nifty', 'bees', 'gold')",
    ),
    limit: int = Query(
        100,
        ge=1,
        le=500,
        description="Maximum number of ETFs to return",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Pagination offset",
    ),
):
    """
    Fetch real-time Indian Exchange Traded Funds (ETFs) from cache.
    """
    etfs, status, age = await market_service.get_etfs(
        category=category, search=search, limit=limit, offset=offset
    )
    response.headers["X-Cache"] = status
    response.headers["X-Data-Age"] = f"{age:.2f}s"
    return etfs
