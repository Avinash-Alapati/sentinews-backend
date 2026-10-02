"""
Application Ports for News Intelligence module.

Protocol-based interfaces decoupling domain and application use cases
from specific infrastructure implementations.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Protocol, Tuple, Union
from uuid import UUID

from app.modules.news_intelligence.domain.entities import NewsArticle


@dataclass
class HoldingWeightView:
    """
    Read-only view model representing a portfolio holding with its current allocation weight.
    Used across module boundaries (Portfolio -> News Intelligence).
    """
    symbol: str
    name: str = ""
    sector: str = "General"
    weight_pct: float = 0.0  # Normalized fraction [0.0, 1.0] or percentage [0.0, 100.0]
    quantity: float = 0.0
    current_price: Optional[float] = None


class PortfolioHoldingsReader(Protocol):
    """
    Narrow read-only port for accessing portfolio holding allocations and weights across module boundaries.
    """
    async def get_holdings_with_weights(
        self, portfolio_id: Union[UUID, int, str]
    ) -> List[HoldingWeightView]:
        """
        Retrieves active holdings and current allocation weights for a specific portfolio.
        """
        ...


class EmbeddingProvider(Protocol):
    """
    Interface for generating semantic vector embeddings.
    """
    def embed_text(self, text: str) -> List[float]:
        """Generates a dense vector embedding for a single text."""
        ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generates dense vector embeddings for a list of texts."""
        ...

    def compute_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Computes cosine similarity between two vector embeddings."""
        ...


class NewsRepository(Protocol):
    """
    Interface for persisting and querying news articles.
    """
    async def get_active_articles(self, limit: int = 50) -> List[NewsArticle]:
        """Fetches active, non-expired articles ordered by publication date."""
        ...

    async def get_latest_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        sector: Optional[str] = None,
        symbol: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> List[NewsArticle]:
        """Fetches non-expired articles with pagination and optional filters."""
        ...

    async def get_trending_articles(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> List[NewsArticle]:
        """Fetches active articles currently marked trending."""
        ...

    async def get_article_by_id(self, article_id: int) -> Optional[NewsArticle]:
        """Fetches a single article by ID."""
        ...

    async def get_article_by_url(self, url: str) -> Optional[NewsArticle]:
        """Fetches a single article by canonical URL."""
        ...

    async def get_existing_urls_and_hashes(
        self,
        urls: List[str],
        hashes: List[str],
    ) -> Tuple[set, set]:
        """Returns existing canonical URLs and content hashes in the database."""
        ...

    async def get_articles_by_symbols_or_sectors(
        self,
        symbols: List[str],
        sectors: List[str],
        limit: int = 50,
    ) -> List[NewsArticle]:
        """Fetches articles matching specified ticker symbols or market sectors."""
        ...

    async def search_by_vector(
        self,
        query_vector: List[float],
        limit: int = 20,
    ) -> List[Tuple[NewsArticle, float]]:
        """
        Performs vector similarity search against article embeddings.
        Returns pairs of (NewsArticle, cosine_similarity).
        """
        ...

    async def save_article(self, article: NewsArticle) -> NewsArticle:
        """Saves or updates a single news article."""
        ...

    async def save_articles_batch(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        """Saves a batch of news articles."""
        ...

    async def update_click_count_and_trending(
        self,
        article_id: int,
        click_count: int,
        is_trending: bool,
        expires_at: datetime,
    ) -> None:
        """Updates article click count, trending flag, and expiration timestamp."""
        ...


class AssetReferenceReader(Protocol):
    """
    Read-only port for accessing known asset tickers, names, and sectors across module boundaries.
    """
    async def get_known_assets(self) -> List[Tuple[str, str, str]]:
        """
        Returns list of (symbol, company_name, sector) for ticker matching.
        """
        ...


class NotificationDispatcher(Protocol):
    """
    Interface for dispatching high-priority user alerts and notifications.
    """
    def send_relevance_alert(
        self,
        portfolio_id: int,
        user_id: int,
        article_id: int,
        symbol: str,
        relevance_score: float,
        article_title: str = "",
    ) -> None:
        """Enqueues or delivers a high-relevance holding alert notification."""
        ...
