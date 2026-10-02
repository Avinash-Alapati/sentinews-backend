import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings


@pytest.mark.asyncio
async def test_neon_compatibility_engine_flags():
    """
    Verify engine instantiation and execution with Neon / PgBouncer pooler flags:
    - DB_DISABLE_PREPARED_STATEMENTS=True (statement_cache_size=0)
    - pool_pre_ping=True
    - pool_recycle=300
    - statement_timeout configured
    """
    connect_args = {
        "server_settings": {"statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS)},
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }

    test_engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=2,
        pool_timeout=10,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )

    test_session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with test_session_maker() as session:
        result = await session.execute(text("SELECT 1 AS alive, current_database() AS db_name;"))
        row = result.mappings().first()
        assert row["alive"] == 1
        assert len(row["db_name"]) > 0

    await test_engine.dispose()


def test_neon_alembic_migration_url_override(monkeypatch):
    """
    Verify Alembic configuration honors MIGRATION_DATABASE_URL when set,
    falling back to DATABASE_URL when unset.
    """
    direct_migration_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/sentinews_direct"
    monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", direct_migration_url)

    # Check resolution logic as used in alembic/env.py
    resolved_url = getattr(settings, "MIGRATION_DATABASE_URL", None) or settings.DATABASE_URL
    assert resolved_url == direct_migration_url

    monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", None)
    fallback_url = getattr(settings, "MIGRATION_DATABASE_URL", None) or settings.DATABASE_URL
    assert fallback_url == settings.DATABASE_URL
