from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings
import app.db.base  # noqa: F401 - Register all models for SQLAlchemy relationships

connect_args = {}
if "asyncpg" in settings.DATABASE_URL or "postgres" in settings.DATABASE_URL:
    server_settings = {
        "statement_timeout": str(getattr(settings, "DB_STATEMENT_TIMEOUT_MS", 5000))
    }
    connect_args["server_settings"] = server_settings
    if getattr(settings, "DB_DISABLE_PREPARED_STATEMENTS", False):
        # Disable prepared statement caching for PgBouncer / Neon transaction poolers
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0

# Create Async Engine with production-ready connection pooling
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.ENVIRONMENT == "development"),
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=getattr(settings, "DB_POOL_PRE_PING", True),
    pool_recycle=getattr(settings, "DB_POOL_RECYCLE", 300),
    connect_args=connect_args,
)

# Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
async_session_factory = AsyncSessionLocal



async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an asynchronous database session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # Only commit if session has pending new, modified, or deleted records
            if bool(session.new or session.dirty or session.deleted):
                await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
