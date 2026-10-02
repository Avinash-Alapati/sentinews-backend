"""
News Intelligence API Router.

SEBI Compliance & Architecture Rules:
1. News creation via public POST endpoints is strictly forbidden. Content enters
   solely via the scheduled automated RSS ingestion pipeline.
2. The only legitimate write on this public API is POST /news/{id}/click (engagement tracking).
3. Every API response schema carries the mandatory SEBI disclaimer:
   "For informational purposes only. Not investment advice. Sentinews is not a SEBI-registered investment adviser or research analyst."
4. Relevance and tone tagging describe chronological relevance and article language,
   never market signals, predictions, or buy/sell recommendations.
"""

from datetime import datetime
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.news.dependencies import get_live_news_service
from app.integrations.news.rss.allowlist import get_allowlisted_feeds
from app.modules.news_intelligence.domain.services.clustering import cluster_articles
from app.modules.news_intelligence.domain.services.live_news_service import LiveNewsService

logger = logging.getLogger("sentinews.api.news")

router = APIRouter(prefix="/news", tags=["News Intelligence"])

# Non-negotiable SEBI disclaimer string
SEBI_MANDATORY_DISCLAIMER = (
    "For informational purposes only. Not investment advice. "
    "Sentinews is not a SEBI-registered investment adviser or research analyst."
)


# ============================================================================
# Response Schemas (All inherit mandatory SEBI disclaimer)
# ============================================================================

class CompliantNewsResponseBase(BaseModel):
    """
    Base schema guaranteeing that every news response carries the mandatory SEBI disclaimer.
    """
    disclaimer: str = Field(
        default=SEBI_MANDATORY_DISCLAIMER,
        description="Mandatory statutory regulatory disclaimer",
    )


class ArticleResponse(CompliantNewsResponseBase):
    """
    SEBI-compliant article metadata response (no full article bodies).
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
    click_count: int = 0
    is_trending: bool = False
    published_at: datetime
    expires_at: Optional[datetime] = None


class RelatedSourceLinkResponse(BaseModel):
    """
    Related publisher link for Full Coverage cluster.
    """
    article_id: Optional[int] = None
    title: str
    source: str
    url: str
    published_at: datetime


class FullCoverageClusterResponse(CompliantNewsResponseBase):
    """
    Full Coverage clustered response grouping multi-publisher reports of the same event.
    """
    primary_article: ArticleResponse
    related_sources: List[RelatedSourceLinkResponse] = Field(default_factory=list)
    cluster_size: int = 1


class PaginatedNewsResponse(CompliantNewsResponseBase):
    """
    Paginated non-expired news feed response.
    """
    items: List[ArticleResponse] = Field(default_factory=list)
    clustered_items: Optional[List[FullCoverageClusterResponse]] = None
    page: int
    limit: int
    total: int


class TrendingNewsResponse(CompliantNewsResponseBase):
    """
    Trending news feed response.
    """
    items: List[ArticleResponse] = Field(default_factory=list)
    page: int
    limit: int
    total: int


class ClickResponse(CompliantNewsResponseBase):
    """
    Engagement event click tracking response.
    """
    article_id: int
    click_count: int
    is_trending: bool


class SourceResponse(BaseModel):
    name: str
    publisher: str
    category: str
    url: str


class FeedSourcesResponse(CompliantNewsResponseBase):
    sources: List[SourceResponse] = Field(default_factory=list)


# ============================================================================
# Endpoints (Live RSS Serving + Engagement Click Tracking)
# ============================================================================

@router.get(
    "/latest",
    response_model=PaginatedNewsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Latest Non-Expired News Articles",
    description="Returns live active financial news articles fetched directly from allowlisted RSS feeds, enriched with tickers, tone, and 72h trending sticky ranking.",
)
@router.get(
    "/feed",
    response_model=PaginatedNewsResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def get_latest_news(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    sector: Optional[str] = Query(default=None, description="Filter by market sector"),
    symbol: Optional[str] = Query(default=None, description="Filter by ticker symbol"),
    tone: Optional[str] = Query(default=None, description="Filter by article tone (positive/neutral/negative)"),
    clustered: bool = Query(default=False, description="Enable Full Coverage clustering"),
    news_service: LiveNewsService = Depends(get_live_news_service),
) -> PaginatedNewsResponse:
    offset = (page - 1) * limit
    articles = await news_service.get_latest_articles(
        limit=limit,
        offset=offset,
        sector=sector,
        symbol=symbol,
        tone=tone,
    )

    article_responses = [
        ArticleResponse(
            id=a.id,
            title=a.title,
            summary=a.summary,
            url=a.url,
            source=a.source,
            symbols=a.symbols,
            sectors=a.sectors,
            article_tone=a.article_tone,
            market_context=a.market_context,
            click_count=a.click_count,
            is_trending=a.is_trending,
            published_at=a.published_at,
            expires_at=a.expires_at,
        )
        for a in articles
    ]

    clustered_responses: Optional[List[FullCoverageClusterResponse]] = None
    if clustered and articles:
        clusters = cluster_articles(articles)
        clustered_responses = []
        for c in clusters:
            prim = c.primary_article
            primary_resp = ArticleResponse(
                id=prim.id,
                title=prim.title,
                summary=prim.summary,
                url=prim.url,
                source=prim.source,
                symbols=prim.symbols,
                sectors=prim.sectors,
                article_tone=prim.article_tone,
                market_context=prim.market_context,
                click_count=prim.click_count,
                is_trending=prim.is_trending,
                published_at=prim.published_at,
                expires_at=prim.expires_at,
            )
            related_resps = [
                RelatedSourceLinkResponse(
                    article_id=r.article_id,
                    title=r.title,
                    source=r.source,
                    url=r.url,
                    published_at=r.published_at,
                )
                for r in c.related_sources
            ]
            clustered_responses.append(
                FullCoverageClusterResponse(
                    primary_article=primary_resp,
                    related_sources=related_resps,
                    cluster_size=c.cluster_size,
                )
            )

    return PaginatedNewsResponse(
        items=article_responses,
        clustered_items=clustered_responses,
        page=page,
        limit=limit,
        total=len(article_responses),
    )


@router.get(
    "/trending",
    response_model=TrendingNewsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Trending News Articles",
    description="Returns active articles with high user interest and clicks (retained at the top for 72h).",
)
async def get_trending_news(
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    news_service: LiveNewsService = Depends(get_live_news_service),
) -> TrendingNewsResponse:
    offset = (page - 1) * limit
    articles = await news_service.get_trending_articles(limit=limit, offset=offset)

    responses = [
        ArticleResponse(
            id=a.id,
            title=a.title,
            summary=a.summary,
            url=a.url,
            source=a.source,
            symbols=a.symbols,
            sectors=a.sectors,
            article_tone=a.article_tone,
            market_context=a.market_context,
            click_count=a.click_count,
            is_trending=a.is_trending,
            published_at=a.published_at,
            expires_at=a.expires_at,
        )
        for a in articles
    ]

    return TrendingNewsResponse(
        items=responses,
        page=page,
        limit=limit,
        total=len(responses),
    )


@router.get(
    "/sources",
    response_model=FeedSourcesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Allowlisted Financial News Sources",
    description="Returns list of SEBI-compliant allowlisted publishers and RSS feeds.",
)
async def get_news_sources() -> FeedSourcesResponse:
    feeds = get_allowlisted_feeds()
    return FeedSourcesResponse(
        sources=[
            SourceResponse(
                name=f.name,
                publisher=f.publisher,
                category=f.category,
                url=f.url,
            )
            for f in feeds
        ]
    )


@router.get(
    "/{id}/full-coverage",
    response_model=FullCoverageClusterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Full Coverage Clustered View for an Article",
    description="Groups near-duplicate articles from multiple publishers covering the same event.",
)
async def get_full_coverage(
    id: int,
    news_service: LiveNewsService = Depends(get_live_news_service),
) -> FullCoverageClusterResponse:
    target = await news_service.get_article_by_id(id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Article with ID {id} not found",
        )

    # Fetch candidate active articles to cluster with
    active_articles = await news_service.get_active_articles(limit=100)
    all_candidates = [target] + [a for a in active_articles if a.id != target.id]
    clusters = cluster_articles(all_candidates)

    target_cluster = next((c for c in clusters if c.primary_article.id == target.id), None)
    if not target_cluster:
        target_cluster = next(
            (c for c in clusters if any(r.article_id == target.id for r in c.related_sources)),
            None,
        )

    if not target_cluster:
        primary_resp = ArticleResponse(
            id=target.id,
            title=target.title,
            summary=target.summary,
            url=target.url,
            source=target.source,
            symbols=target.symbols,
            sectors=target.sectors,
            article_tone=target.article_tone,
            market_context=target.market_context,
            click_count=target.click_count,
            is_trending=target.is_trending,
            published_at=target.published_at,
            expires_at=target.expires_at,
        )
        return FullCoverageClusterResponse(primary_article=primary_resp, related_sources=[], cluster_size=1)

    prim = target_cluster.primary_article
    primary_resp = ArticleResponse(
        id=prim.id,
        title=prim.title,
        summary=prim.summary,
        url=prim.url,
        source=prim.source,
        symbols=prim.symbols,
        sectors=prim.sectors,
        article_tone=prim.article_tone,
        market_context=prim.market_context,
        click_count=prim.click_count,
        is_trending=prim.is_trending,
        published_at=prim.published_at,
        expires_at=prim.expires_at,
    )
    related_resps = [
        RelatedSourceLinkResponse(
            article_id=r.article_id,
            title=r.title,
            source=r.source,
            url=r.url,
            published_at=r.published_at,
        )
        for r in target_cluster.related_sources
    ]

    return FullCoverageClusterResponse(
        primary_article=primary_resp,
        related_sources=related_resps,
        cluster_size=target_cluster.cluster_size,
    )


@router.post(
    "/{id}/click",
    response_model=ClickResponse,
    status_code=status.HTTP_200_OK,
    summary="Record Article Click and Evaluate 72h Trending Policy",
    description="Increments engagement click counter for an article card. If engagement crosses threshold, pins card at the top for 72 hours.",
)
async def click_article(
    id: int,
    news_service: LiveNewsService = Depends(get_live_news_service),
) -> ClickResponse:
    clicks, is_trending = await news_service.record_click(article_id=id)
    return ClickResponse(
        article_id=id,
        click_count=clicks,
        is_trending=is_trending,
    )
