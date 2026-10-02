"""
API tests for Authentication endpoints (register, login, JWT protection, Google OAuth fallback).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.auth.dependencies import get_refresh_token_repository, get_user_repository
from app.cache.market_cache import market_cache
from app.db.base import Base
from app.main import app
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    SQLAlchemyRefreshTokenRepository,
)
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


@pytest.fixture
async def test_app_client():
    market_cache._memory_cache.clear()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_user_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyUserRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_refresh_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyRefreshTokenRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_user_repository] = override_get_user_repo
    app.dependency_overrides[get_refresh_token_repository] = override_get_refresh_repo

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_auth_full_registration_and_jwt_verification(test_app_client: AsyncClient):
    # 1. Register
    reg_payload = {
        "email": "retail.investor@sentinews.in",
        "password": "StrongPassword999!",
        "full_name": "Priya Sharma",
    }
    reg_res = await test_app_client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201
    reg_data = reg_res.json()
    assert "access_token" in reg_data
    assert reg_data["user"]["email"] == "retail.investor@sentinews.in"
    assert reg_data["user"]["full_name"] == "Priya Sharma"

    # Duplicate registration should 409
    dup_res = await test_app_client.post("/api/v1/auth/register", json=reg_payload)
    assert dup_res.status_code == 409

    # 2. Login
    login_payload = {
        "email": "retail.investor@sentinews.in",
        "password": "StrongPassword999!",
    }
    login_res = await test_app_client.post("/api/v1/auth/login", json=login_payload)
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    assert bool(token) is True

    # Bad password login should 401
    bad_login = await test_app_client.post(
        "/api/v1/auth/login",
        json={"email": "retail.investor@sentinews.in", "password": "WrongPassword!"},
    )
    assert bad_login.status_code == 401

    # 3. Authenticate to protected /me endpoint using JWT
    me_res = await test_app_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["email"] == "retail.investor@sentinews.in"
    assert me_data["full_name"] == "Priya Sharma"

    # Request without token should 401
    unauth_res = await test_app_client.get("/api/v1/auth/me")
    assert unauth_res.status_code == 401


@pytest.mark.asyncio
async def test_google_login_unconfigured_returns_501(test_app_client: AsyncClient):
    """Google OAuth endpoint should cleanly return 501 Not Implemented when GOOGLE_CLIENT_ID is unset."""
    from app.api.v1.auth.dependencies import get_google_oauth_provider
    from app.modules.auth.infrastructure.oauth.google import GoogleOAuthProvider
    
    mock_provider = GoogleOAuthProvider(client_id="", client_secret="")
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider
    try:
        res = await test_app_client.post(
            "/api/v1/auth/google",
            json={"id_token": "dummy_google_id_token"},
        )
        assert res.status_code == 501
        assert "GOOGLE_CLIENT_ID" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_google_oauth_provider, None)
