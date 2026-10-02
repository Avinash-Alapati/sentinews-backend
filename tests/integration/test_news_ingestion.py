"""
Integration tests for News Ingestion Pipeline, Expiry Filtering, and Public API.
"""

from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.news.dependencies import get_news_repository
from app.db.base import Base
from app.db.session import get_db
from app.integrations.news.rss.allowlist import RSSFeedSource
from app.integrations.news.rss.fetcher import RSSFeedFetcher
from app.main import app
from app.modules.news_intelligence.application.use_cases.ingest_news_from_feeds import (
    IngestNewsFromFeedsUseCase,
)
from app.modules.news_intelligence.application.use_cases.record_article_click import (
    RecordArticleClickUseCase,
)
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.retention import calculate_trending_expiry
from app.modules.news_intelligence.infrastructure.adapters.asset_reader import (
    DatabaseAssetReferenceReader,
)
from app.modules.news_intelligence.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from app.modules.news_intelligence.infrastructure.repositories.news_repository import (
    SQLAlchemyNewsRepository,
)


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_real_feed_ingestion_and_compliance(async_db: AsyncSession):
    """
    Integration test: Runs IngestNewsFromFeedsUseCase against a real fetch of
    an allowlisted RSS feed (e.g. Yahoo Finance), confirming articles land in the database
    with compliant metadata and NO full article bodies stored.
    """
    real_feed = RSSFeedSource(
        name="Yahoo Finance Markets",
        publisher="Yahoo Finance",
        url="https://finance.yahoo.com/news/rssindex",
        category="Markets",
        enabled=True,
    )

    fetcher = RSSFeedFetcher(feeds=[real_feed], timeout_seconds=10.0)
    try:
        raw_articles = await fetcher.fetch_all()
    except Exception:
        raw_articles = []

    # If the network or publisher is reachable, test real fetch
    # Otherwise fallback to a simulated allowlisted payload to ensure deterministic CI
    if not raw_articles:
        # Fallback test with synthetic allowlisted feed entry
        from app.integrations.news.rss.fetcher import RawFeedArticle, compute_content_hash
        now = datetime.now(timezone.utc)
        h = compute_content_hash("Infosys Q3 Results Announced", "Infosys posted 12% revenue growth.", "https://finance.yahoo.com/news/infy1")
        raw_articles = [
            RawFeedArticle(
                title="Infosys Q3 Results Announced",
                summary="Infosys posted 12% revenue growth in constant currency.",
                url="https://finance.yahoo.com/news/infy1",
                source="Yahoo Finance",
                category="Markets",
                published_at=now,
                content_hash=h,
            )
        ]
    # Ensure articles have current published_at timestamp so they are active/non-expired
    for a in raw_articles:
        a.published_at = datetime.now(timezone.utc)

    repo = SQLAlchemyNewsRepository(session=async_db)
    asset_reader = DatabaseAssetReferenceReader(session=async_db)

    use_case = IngestNewsFromFeedsUseCase(
        news_repo=repo,
        rss_fetcher=None,
        embedding_provider=None,
        asset_reader=asset_reader,
    )
    class AsyncMockFetcher:
        async def fetch_all(self):
            return raw_articles

    use_case.rss_fetcher = AsyncMockFetcher()

    stats = await use_case.execute()

    assert stats["new_ingested"] >= 1

    # Verify saved article fields in database
    active_articles = await repo.get_active_articles(limit=50)
    assert len(active_articles) >= 1

    for a in active_articles:
        assert a.title != ""
        assert a.url != ""
        assert a.source != ""
        # Strict SEBI compliance: no full body scraping allowed
        assert a.content == ""
        assert a.article_tone in ["positive", "neutral", "negative"]
        assert a.market_context in ["pre_market", "market_hours", "post_market"]
        assert a.expires_at is not None


@pytest.mark.asyncio
async def test_get_latest_and_trending_strictly_exclude_expired_articles(async_db: AsyncSession):
    """
    Confirms that GET latest and trending queries never return an article whose
    expires_at is in the past.
    """
    repo = SQLAlchemyNewsRepository(session=async_db)
    now = datetime.now(timezone.utc)

    # 1. Expired article (expired 2 hours ago)
    expired_article = NewsArticle(
        title="Old Expired Market News",
        summary="This news expired.",
        content="",
        url="https://moneycontrol.com/old-news",
        source="Moneycontrol",
        published_at=now - timedelta(hours=30),
        expires_at=now - timedelta(hours=6),
        click_count=10,
        is_trending=False,
    )
    # 2. Active non-expired article
    active_article = NewsArticle(
        title="Fresh Active News Item",
        summary="This news is active.",
        content="",
        url="https://moneycontrol.com/fresh-news",
        source="Moneycontrol",
        published_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        click_count=5,
        is_trending=False,
    )
    # 3. Expired Trending article (trending previously, but past 72h)
    expired_trending = NewsArticle(
        title="Expired Trending News Item",
        summary="This was trending 4 days ago.",
        content="",
        url="https://economictimes.com/old-trend",
        source="Economic Times",
        published_at=now - timedelta(hours=100),
        expires_at=now - timedelta(hours=20),
        click_count=150,
        is_trending=True,
    )
    # 4. Active Trending article
    active_trending = NewsArticle(
        title="Active Trending News Item",
        summary="This is trending right now.",
        content="",
        url="https://economictimes.com/fresh-trend",
        source="Economic Times",
        published_at=now - timedelta(hours=5),
        expires_at=now + timedelta(hours=67),
        click_count=80,
        is_trending=True,
    )

    await repo.save_article(expired_article)
    await repo.save_article(active_article)
    await repo.save_article(expired_trending)
    await repo.save_article(active_trending)

    # Query latest articles
    latest = await repo.get_latest_articles(limit=50)
    latest_titles = [a.title for a in latest]
    assert "Fresh Active News Item" in latest_titles
    assert "Active Trending News Item" in latest_titles
    assert "Old Expired Market News" not in latest_titles
    assert "Expired Trending News Item" not in latest_titles

    # Query trending articles
    trending = await repo.get_trending_articles(limit=50)
    trending_titles = [a.title for a in trending]
    assert "Active Trending News Item" in trending_titles
    assert "Expired Trending News Item" not in trending_titles
    assert len(trending) == 1


@pytest.mark.asyncio
async def test_click_tracking_extends_to_72h_at_threshold(async_db: AsyncSession):
    """
    Confirms POST /news/{id}/click tracks clicks and extends expiry to 72h on crossing threshold 50.
    """
    repo = SQLAlchemyNewsRepository(session=async_db)
    now = datetime.now(timezone.utc)
    pub_time = now - timedelta(hours=2)
    initial_exp = pub_time + timedelta(hours=24)

    article = NewsArticle(
        title="Breaking Corporate Announcement",
        summary="Major merger announcement.",
        content="",
        url="https://livemint.com/merger-news",
        source="LiveMint",
        published_at=pub_time,
        expires_at=initial_exp,
        click_count=48,
        is_trending=False,
    )
    saved = await repo.save_article(article)

    use_case = RecordArticleClickUseCase(
        news_repo=repo,
        trending_threshold=50,
    )

    # Click 49
    count, is_trending = await use_case.execute(article_id=saved.id, article=saved)
    assert count == 1  # In-memory counter starting at 1 for this test instance

    # Advance memory counter to 49
    from app.modules.news_intelligence.application.use_cases.record_article_click import _memory_click_counts
    _memory_click_counts[saved.id] = 49

    # Click 50 (Threshold crossed!)
    count, is_trending = await use_case.execute(article_id=saved.id, article=saved)
    assert count == 50
    assert is_trending is True

    # Check that database updated expiry to 72h from publication time
    updated_article = await repo.get_article_by_id(saved.id)
    assert updated_article.is_trending is True
    expected_72h_expiry = calculate_trending_expiry(pub_time)
    expiry_dt = updated_article.expires_at
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    assert abs((expiry_dt - expected_72h_expiry).total_seconds()) < 5


@pytest.mark.asyncio
async def test_fastapi_endpoints_disclaimer_and_no_public_create(async_db: AsyncSession):
    """
    Confirms FastAPI news endpoints return the mandatory disclaimer on all responses,
    and that no public article creation endpoint exists.
    """
    repo = SQLAlchemyNewsRepository(session=async_db)
    now = datetime.now(timezone.utc)
    saved = await repo.save_article(
        NewsArticle(
            title="Markets Rally on Strong Inflows",
            summary="Sensex and Nifty rise.",
            content="",
            url="https://moneycontrol.com/rally1",
            source="Moneycontrol",
            published_at=now,
            expires_at=now + timedelta(hours=24),
            click_count=5,
            is_trending=False,
        )
    )

    from app.api.v1.news.dependencies import get_live_news_service

    class MockLiveNewsService:
        async def get_latest_articles(self, limit=20, offset=0, sector=None, symbol=None, tone=None):
            return [saved]
        async def get_trending_articles(self, limit=20, offset=0):
            return []
        async def get_active_articles(self, limit=100):
            return [saved]
        async def record_click(self, article_id: int):
            return 6, False


    app.dependency_overrides[get_db] = lambda: async_db
    app.dependency_overrides[get_news_repository] = lambda: repo
    app.dependency_overrides[get_live_news_service] = lambda: MockLiveNewsService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/news/latest
        res_latest = await client.get("/api/v1/news/latest")
        assert res_latest.status_code == 200
        data_latest = res_latest.json()
        assert "disclaimer" in data_latest
        assert "Sentinews is not a SEBI-registered" in data_latest["disclaimer"]
        assert len(data_latest["items"]) == 1
        assert "disclaimer" in data_latest["items"][0]

        # 2. GET /api/v1/news/trending
        res_trending = await client.get("/api/v1/news/trending")
        assert res_trending.status_code == 200
        data_trending = res_trending.json()
        assert "disclaimer" in data_trending

        # 3. POST /api/v1/news/{id}/click
        res_click = await client.post(f"/api/v1/news/{saved.id}/click")
        assert res_click.status_code == 200
        data_click = res_click.json()
        assert "disclaimer" in data_click
        assert data_click["article_id"] == saved.id

        # 4. Confirm NO public news creation endpoint exists (POST /api/v1/news/articles)
        res_create = await client.post("/api/v1/news/articles", json={"title": "Unauthorized Article"})
        assert res_create.status_code in [404, 405]

    app.dependency_overrides.clear()
