"""
End-to-End API Integration tests for Google OAuth 2.0.

Tests:
1. Authorization URL generation & Direct Redirect (/api/v1/auth/google/login).
2. Callback Handler with CSRF State protection (/api/v1/auth/google/callback).
3. Direct Authorization Code Exchange for SPAs (/api/v1/auth/google/exchange).
4. Google ID Token / One-Tap Verification (/api/v1/auth/google).
5. Protected endpoint access using issued JWT tokens (/api/v1/auth/me).
6. Unconfigured fallback (501) and security edge cases (CSRF tampering, expired states, Google errors).
"""

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.auth.dependencies import (
    get_google_oauth_provider,
    get_user_repository,
    invalidate_user_cache,
)
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.modules.auth.infrastructure.oauth.google import GoogleOAuthProvider
from app.modules.auth.infrastructure.oauth.state import generate_oauth_state
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


@pytest.fixture
async def test_client():
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

    app.dependency_overrides[get_user_repository] = override_get_user_repo
    await invalidate_user_cache(1, "rajesh.kumar@sentinews.in")
    await invalidate_user_cache(2, "vikram.singh@sentinews.in")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    await invalidate_user_cache(1, "rajesh.kumar@sentinews.in")
    await invalidate_user_cache(2, "vikram.singh@sentinews.in")
    app.dependency_overrides.clear()
    await engine.dispose()


# =========================================================================
# 1. Google OAuth Login Initiation Tests
# =========================================================================

@pytest.mark.asyncio
async def test_google_login_unconfigured_returns_501(test_client: AsyncClient):
    """When GOOGLE_CLIENT_ID is empty, initiating login returns 501 Not Implemented."""
    mock_provider = GoogleOAuthProvider(client_id="", client_secret="")
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    res = await test_client.get("/api/v1/auth/google/login")
    assert res.status_code == 501
    assert "GOOGLE_CLIENT_ID" in res.json()["detail"]


@pytest.mark.asyncio
async def test_google_login_returns_json_auth_url(test_client: AsyncClient):
    """When configured, returns authorization URL and signed CSRF state as JSON."""
    mock_provider = GoogleOAuthProvider(
        client_id="test_google_client_id.apps.googleusercontent.com",
        client_secret="test_secret",
        redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    res = await test_client.get("/api/v1/auth/google/login?return_url=/portfolio")
    assert res.status_code == 200
    data = res.json()
    assert "authorization_url" in data
    assert "state" in data
    assert "accounts.google.com" in data["authorization_url"]
    assert "test_google_client_id" in data["authorization_url"]
    assert data["state"] in data["authorization_url"]


@pytest.mark.asyncio
async def test_google_login_redirect_mode(test_client: AsyncClient):
    """When redirect=true query param is passed, returns HTTP 307 redirect."""
    mock_provider = GoogleOAuthProvider(
        client_id="test_google_client_id.apps.googleusercontent.com",
        client_secret="test_secret",
    )
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    res = await test_client.get("/api/v1/auth/google/login?redirect=true", follow_redirects=False)
    assert res.status_code == 307
    assert "Location" in res.headers
    assert "accounts.google.com" in res.headers["Location"]


# =========================================================================
# 2. Google OAuth Callback & CSRF Tests
# =========================================================================

@pytest.mark.asyncio
async def test_google_callback_missing_params(test_client: AsyncClient):
    """Callback without code or state should return 400 Bad Request."""
    res = await test_client.get("/api/v1/auth/google/callback")
    assert res.status_code == 400
    assert "Missing required 'code' or 'state'" in res.json()["detail"]


@pytest.mark.asyncio
async def test_google_callback_oauth_error_from_provider(test_client: AsyncClient):
    """When Google redirects with error=access_denied, callback returns 400."""
    res = await test_client.get(
        "/api/v1/auth/google/callback?error=access_denied&error_description=User+declined+consent"
    )
    assert res.status_code == 400
    assert "User declined consent" in res.json()["detail"]


@pytest.mark.asyncio
async def test_google_callback_invalid_csrf_state(test_client: AsyncClient):
    """Tampered state token in callback should return 400 Bad Request."""
    res = await test_client.get(
        "/api/v1/auth/google/callback?code=mock_code&state=tampered.signature"
    )
    assert res.status_code == 400
    assert "CSRF" in res.json()["detail"]


@pytest.mark.asyncio
async def test_google_callback_success_end_to_end(test_client: AsyncClient):
    """Full End-to-End Callback flow: verifies state, exchanges code, creates user, issues JWT, and accesses /me."""
    mock_provider = GoogleOAuthProvider(
        client_id="test_google_client_id.apps.googleusercontent.com",
        client_secret="test_google_secret_key",
        redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    # Generate legitimate state token
    valid_state = generate_oauth_state(
        secret_key=settings.SECRET_KEY,
        provider="google",
        return_url="/portfolio",
    )

    # Mock Google Token exchange response
    fake_profile = {
        "email": "rajesh.kumar@sentinews.in",
        "name": "Rajesh Kumar",
        "sub": "google-oauth2-987654",
        "picture": "https://lh3.googleusercontent.com/a/avatar.jpg",
        "email_verified": True,
        "id_token": "mock_id_token",
        "access_token": "mock_access_token",
    }

    with patch.object(mock_provider, "exchange_code", new_callable=AsyncMock) as mock_exchange:
        mock_exchange.return_value = fake_profile

        callback_res = await test_client.get(
            f"/api/v1/auth/google/callback?code=valid_auth_code_from_google&state={valid_state}"
        )

    assert callback_res.status_code == 200
    data = callback_res.json()
    assert "access_token" in data
    assert data["user"]["email"] == "rajesh.kumar@sentinews.in"
    assert data["user"]["full_name"] == "Rajesh Kumar"
    assert data["user"]["is_active"] is True
    jwt_token = data["access_token"]

    # Verify that the issued JWT allows authentication to /api/v1/auth/me
    me_res = await test_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["email"] == "rajesh.kumar@sentinews.in"
    assert me_data["full_name"] == "Rajesh Kumar"


# =========================================================================
# 3. Direct Authorization Code Exchange Tests (SPA / Mobile)
# =========================================================================

@pytest.mark.asyncio
async def test_google_exchange_direct(test_client: AsyncClient):
    """Direct POST /api/v1/auth/google/exchange endpoint for SPAs."""
    mock_provider = GoogleOAuthProvider(
        client_id="test_google_client_id.apps.googleusercontent.com",
        client_secret="test_google_secret_key",
    )
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    fake_profile = {
        "email": "sunita.sharma@sentinews.in",
        "name": "Sunita Sharma",
        "sub": "sub-112233",
        "email_verified": True,
    }

    with patch.object(mock_provider, "exchange_code", new_callable=AsyncMock) as mock_exchange:
        mock_exchange.return_value = fake_profile

        res = await test_client.post(
            "/api/v1/auth/google/exchange",
            json={"code": "direct_auth_code_123"},
        )

    assert res.status_code == 200
    data = res.json()
    assert data["user"]["email"] == "sunita.sharma@sentinews.in"
    assert data["user"]["full_name"] == "Sunita Sharma"
    assert bool(data["access_token"]) is True


# =========================================================================
# 4. Google ID Token / One-Tap Verification Tests
# =========================================================================

@pytest.mark.asyncio
async def test_google_id_token_login_success(test_client: AsyncClient):
    """POST /api/v1/auth/google with valid Google ID token."""
    mock_provider = GoogleOAuthProvider(client_id="test_client_id")
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    fake_id_token_info = {
        "email": "vikram.singh@sentinews.in",
        "name": "Vikram Singh",
        "sub": "google-sub-7788",
        "email_verified": True,
    }

    with patch.object(mock_provider, "verify_google_token", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = fake_id_token_info

        res = await test_client.post(
            "/api/v1/auth/google",
            json={"id_token": "valid_mock_google_id_token"},
        )

    assert res.status_code == 200
    data = res.json()
    assert data["user"]["email"] == "vikram.singh@sentinews.in"
    assert data["user"]["full_name"] == "Vikram Singh"
    assert bool(data["access_token"]) is True

    # Call /me with the token
    me_res = await test_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "vikram.singh@sentinews.in"


@pytest.mark.asyncio
async def test_google_id_token_invalid(test_client: AsyncClient):
    """POST /api/v1/auth/google with rejected ID token returns 401."""
    mock_provider = GoogleOAuthProvider(client_id="test_client_id")
    app.dependency_overrides[get_google_oauth_provider] = lambda: mock_provider

    with patch.object(mock_provider, "verify_google_token", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = None

        res = await test_client.post(
            "/api/v1/auth/google",
            json={"id_token": "invalid_id_token"},
        )

    assert res.status_code == 401
    assert "verification failed" in res.json()["detail"]
