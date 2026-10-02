# Import all the models, so that Base has them before being
# imported by Alembic
from app.db.base_class import Base  # noqa
from app.db.models.user import User  # noqa
from app.db.models.watchlist import Watchlist, WatchlistItem  # noqa
from app.db.models.portfolio import PortfolioORM, HoldingORM, TransactionORM  # noqa
from app.db.models.news import NewsArticleORM  # noqa
from app.db.models.market_report import MarketReportORM  # noqa
from app.db.models.refresh_token import RefreshTokenORM  # noqa
