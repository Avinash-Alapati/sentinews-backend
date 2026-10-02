from fastapi import APIRouter
from app.api.v1.auth import router as auth
from app.api.v1.endpoints import market, health
from app.api.v1.market_reports import router as market_reports
from app.api.v1.news import router as news
from app.api.v1.portfolio import router as portfolio
from app.api.v1.watchlist import router as watchlist
from app.api.v1.websocket import router as ws_router

api_router = APIRouter()

# Register endpoint routers
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(market.router)
api_router.include_router(portfolio.router)
api_router.include_router(news.router)
api_router.include_router(market_reports.router)
api_router.include_router(watchlist.router)
api_router.include_router(ws_router)


