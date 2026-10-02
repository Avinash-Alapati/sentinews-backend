import asyncio
import uuid
import pytest
from unittest.mock import patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings
from app.db.base import Base
from app.modules.auth.application.use_cases.refresh import RefreshUseCase
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    SQLAlchemyRefreshTokenRepository,
    RefreshTokenReusedError,
    InvalidRefreshTokenError,
)
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


@pytest.fixture
async def real_postgres_session_factory():
    """Provides a connection pool against the real PostgreSQL container."""
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=5,
        pool_timeout=10,
        pool_pre_ping=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_refresh_concurrency_zero_grace(real_postgres_session_factory):
    """
    Test refresh token concurrency against REAL PostgreSQL with grace=0:
    Fires two simultaneous refreshes via asyncio.gather against independent sessions in the connection pool.
    Asserts EXACTLY 1 winner succeeds and the concurrent duplicate is rejected as reuse.
    """
    test_email = f"concurrent.zero.grace.{uuid.uuid4().hex[:8]}@sentinews.in"
    
    # 1. Create user and initial token
    async with real_postgres_session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session=session)
        user = await user_repo.create(User(email=test_email, hashed_password="secure_password_hash"))
        refresh_repo = SQLAlchemyRefreshTokenRepository(session=session)
        token = await refresh_repo.create_token(user_id=user.id)
        await session.commit()

    # 2. Concurrency workers
    async def do_refresh():
        async with real_postgres_session_factory() as session:
            u_repo = SQLAlchemyUserRepository(session=session)
            r_repo = SQLAlchemyRefreshTokenRepository(session=session)
            uc = RefreshUseCase(
                user_repo=u_repo,
                refresh_repo=r_repo,
                secret_key=settings.SECRET_KEY,
                algorithm=settings.ALGORITHM,
            )
            res = await uc.execute(refresh_token=token)
            await session.commit()
            return res

    with patch.object(settings, "REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 0):
        results = await asyncio.gather(do_refresh(), do_refresh(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    print(f"\n[Real-Postgres Concurrency Grace=0] Successes: {len(successes)}, Failures: {len(failures)}")
    assert len(successes) == 1, f"Expected 1 winner, got {len(successes)}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
    assert isinstance(failures[0], (RefreshTokenReusedError, InvalidRefreshTokenError))


@pytest.mark.asyncio
async def test_real_postgres_refresh_concurrency_within_grace_window(real_postgres_session_factory):
    """
    Test refresh token concurrency against REAL PostgreSQL within 10s grace window:
    Fires two simultaneous refreshes via asyncio.gather against independent sessions in the connection pool.
    Asserts BOTH succeed within the grace window (supporting multi-tab SPA refresh bursts).
    """
    test_email = f"concurrent.grace.window.{uuid.uuid4().hex[:8]}@sentinews.in"

    # 1. Create user and initial token
    async with real_postgres_session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session=session)
        user = await user_repo.create(User(email=test_email, hashed_password="secure_password_hash"))
        refresh_repo = SQLAlchemyRefreshTokenRepository(session=session)
        token = await refresh_repo.create_token(user_id=user.id)
        await session.commit()

    # 2. Concurrency workers
    async def do_refresh():
        async with real_postgres_session_factory() as session:
            u_repo = SQLAlchemyUserRepository(session=session)
            r_repo = SQLAlchemyRefreshTokenRepository(session=session)
            uc = RefreshUseCase(
                user_repo=u_repo,
                refresh_repo=r_repo,
                secret_key=settings.SECRET_KEY,
                algorithm=settings.ALGORITHM,
            )
            res = await uc.execute(refresh_token=token)
            await session.commit()
            return res

    with patch.object(settings, "REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 10):
        results = await asyncio.gather(do_refresh(), do_refresh(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    print(f"\n[Real-Postgres Concurrency Grace=10s] Successes: {len(successes)}, Failures: {len(failures)}")
    assert len(successes) == 2, f"Expected both to succeed within grace window, got failures: {failures}"
    assert len(failures) == 0

    user1, access1, new_token1 = successes[0]
    user2, access2, new_token2 = successes[1]
    assert user1.id == user.id
    assert user2.id == user.id
    assert bool(access1) and bool(access2)
