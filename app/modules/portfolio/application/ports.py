"""
Application Ports for the Portfolio module.

Protocol-based interfaces decoupling domain logic from repositories,
market data providers, and inter-module events.
"""

from typing import Dict, List, Optional, Protocol

from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction


class PortfolioRepository(Protocol):
    """
    Interface for portfolio persistence and querying.
    """
    async def get_portfolio(self, portfolio_id: int) -> Optional[Portfolio]:
        """Loads a portfolio with its holdings and transactions."""
        ...

    async def get_portfolio_metadata(self, portfolio_id: int) -> Optional[Portfolio]:
        """Loads only portfolio metadata without loading child collections (holdings and transactions)."""
        ...

    async def get_portfolio_by_user(self, user_id: int) -> Optional[Portfolio]:
        """Loads a user's portfolio with relations."""
        ...

    async def get_portfolio_by_user_metadata(self, user_id: int) -> Optional[Portfolio]:
        """Loads a user's portfolio metadata without loading child collections."""
        ...

    async def get_holdings_with_weights(self, portfolio_id: int) -> List[Holding]:
        """
        Retrieves portfolio holdings with their current calculated allocation weights.
        Used across module boundaries (e.g. by news intelligence feed generation).
        """
        ...

    async def get_holdings(self, portfolio_id: int) -> List[Holding]:
        """Retrieves raw holdings for a portfolio."""
        ...

    async def get_transactions(
        self,
        portfolio_id: int,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Transaction]:
        """Retrieves transactions for a portfolio in chronological order with optional SQL LIMIT and OFFSET."""
        ...

    async def add_holding(self, portfolio_id: int, holding: Holding) -> Holding:
        """Adds a holding to a portfolio."""
        ...

    async def add_transaction(self, portfolio_id: int, transaction: Transaction) -> Transaction:
        """Records a new buy or sell transaction."""
        ...

    async def save_portfolio(self, portfolio: Portfolio) -> Portfolio:
        """Persists or updates portfolio state."""
        ...

    async def delete_portfolio(self, portfolio_id: int) -> bool:
        """Deletes a portfolio and all associated holdings and transactions."""
        ...


class MarketDataProvider(Protocol):
    """
    Interface for fetching equity market quotes.
    """
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Fetches the latest price for a ticker symbol."""
        ...

    async def get_quotes_batch(self, symbols: List[str]) -> Dict[str, float]:
        """Fetches latest prices for multiple symbols in a batch."""
        ...


class NewsIntelligenceRelevancePort(Protocol):
    """
    Port for notifying news intelligence when portfolio composition changes.
    """
    async def notify_holding_added(self, holding: Holding) -> None:
        """
        Embeds asset profile and backfills relevance scores against existing news corpus.
        """
        ...
