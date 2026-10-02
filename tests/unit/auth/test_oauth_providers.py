"""
Unit tests for Google OAuth Provider, CSRF State management, and Use Cases.
"""

import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.modules.auth.domain.entities import User
from app.modules.auth.application.use_cases.google_login import (
    GoogleAuthFailedError,
    GoogleLoginUseCase,
)
from app.modules.auth.infrastructure.oauth.google import (
    GoogleOAuthError,
    GoogleOAuthNotConfiguredError,
    GoogleOAuthProvider,
)
from app.modules.auth.infrastructure.oauth.state import (
    InvalidOAuthStateError,
    generate_oauth_state,
    verify_oauth_state,
)


# =========================================================================
# 1. CSRF State Token Tests
# =========================================================================

def test_oauth_state_lifecycle():
    secret_key = "super_secret_test_key"
    state = generate_oauth_state(
        secret_key=secret_key,
        provider="google",
        return_url="/dashboard/portfolio",
        expires_in_seconds=60,
    )
    assert bool(state) is True
    assert "." in state

    payload = verify_oauth_state(state=state, secret_key=secret_key, expected_provider="google")
    assert payload["provider"] == "google"
    assert payload["return_url"] == "/dashboard/portfolio"
    assert payload["exp"] > time.time()


def test_oauth_state_tampering_fails():
    secret_key = "super_secret_test_key"
    state = generate_oauth_state(secret_key=secret_key, provider="google")
    parts = state.split(".")
    tampered_state = f"{parts[0]}extra.{parts[1]}"

    with pytest.raises(InvalidOAuthStateError, match="signature mismatch|potential CSRF"):
        verify_oauth_state(state=tampered_state, secret_key=secret_key, expected_provider="google")


def test_oauth_state_expired_fails():
    secret_key = "super_secret_test_key"
    state = generate_oauth_state(
        secret_key=secret_key,
        provider="google",
        expires_in_seconds=-1,  # Expired in past
    )
    with pytest.raises(InvalidOAuthStateError, match="expired"):
        verify_oauth_state(state=state, secret_key=secret_key, expected_provider="google")


def test_oauth_state_provider_mismatch():
    secret_key = "super_secret_test_key"
    state = generate_oauth_state(secret_key=secret_key, provider="github")
    with pytest.raises(InvalidOAuthStateError, match="provider mismatch"):
        verify_oauth_state(state=state, secret_key=secret_key, expected_provider="google")


def test_oauth_state_malformed():
    with pytest.raises(InvalidOAuthStateError):
        verify_oauth_state(state="invalid_token_without_dot", secret_key="key", expected_provider="google")


# =========================================================================
# 2. Google OAuth Provider Unit Tests
# =========================================================================

def test_google_provider_unconfigured():
    provider = GoogleOAuthProvider(client_id="", client_secret="")
    assert provider.is_configured is False
    assert provider.is_full_oauth_configured is False

    with pytest.raises(GoogleOAuthNotConfiguredError):
        provider.get_authorization_url(state="test_state")


def test_google_provider_authorization_url():
    provider = GoogleOAuthProvider(
        client_id="test_client_id_123.apps.googleusercontent.com",
        client_secret="test_secret",
        redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )
    assert provider.is_configured is True
    assert provider.is_full_oauth_configured is True

    url = provider.get_authorization_url(state="secure_csrf_state_xyz")
    assert "https://accounts.google.com/o/oauth2/v2/auth" in url
    assert "client_id=test_client_id_123.apps.googleusercontent.com" in url
    assert "state=secure_csrf_state_xyz" in url
    assert "response_type=code" in url
    assert "redirect_uri=http" in url


@pytest.mark.asyncio
async def test_verify_google_token_success():
    provider = GoogleOAuthProvider(client_id="my_client_id")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "aud": "my_client_id",
        "email": "investor@example.com",
        "name": "Rohan Mehta",
        "sub": "google-sub-12345",
        "email_verified": "true",
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        info = await provider.verify_google_token("valid_id_token_xyz")

    assert info is not None
    assert info["email"] == "investor@example.com"
    assert info["name"] == "Rohan Mehta"
    assert info["email_verified"] is True


@pytest.mark.asyncio
async def test_verify_google_token_aud_mismatch():
    provider = GoogleOAuthProvider(client_id="my_client_id")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "aud": "different_client_id",
        "email": "hacker@example.com",
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        info = await provider.verify_google_token("bad_aud_token")

    assert info is None


@pytest.mark.asyncio
async def test_exchange_code_success():
    provider = GoogleOAuthProvider(
        client_id="my_client_id",
        client_secret="my_client_secret",
        redirect_uri="http://localhost:8000/callback",
    )

    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "access_token": "ya29.google_access_token_123",
        "id_token": "google_id_token_123",
    }

    verified_info = {
        "aud": "my_client_id",
        "email": "ananya.patel@sentinews.in",
        "name": "Ananya Patel",
        "sub": "sub-998877",
        "email_verified": True,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch.object(provider, "verify_google_token", new_callable=AsyncMock) as mock_verify:
        mock_post.return_value = token_resp
        mock_verify.return_value = verified_info

        profile = await provider.exchange_code(code="auth_code_xyz")

    assert profile["email"] == "ananya.patel@sentinews.in"
    assert profile["name"] == "Ananya Patel"
    assert profile["access_token"] == "ya29.google_access_token_123"


@pytest.mark.asyncio
async def test_exchange_code_google_rejection():
    provider = GoogleOAuthProvider(client_id="cid", client_secret="csecret")
    token_resp = MagicMock()
    token_resp.status_code = 400
    token_resp.text = '{"error": "invalid_grant", "error_description": "Code expired"}'
    token_resp.json.return_value = {"error": "invalid_grant", "error_description": "Code expired"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = token_resp
        with pytest.raises(GoogleOAuthError, match="Code expired"):
            await provider.exchange_code(code="expired_code")


# =========================================================================
# 3. Google Login Use Case Tests
# =========================================================================

@pytest.mark.asyncio
async def test_google_login_provisions_new_user():
    user_repo = AsyncMock()
    user_repo.get_by_email.return_value = None
    created_user = User(
        id=42,
        email="newuser@example.com",
        hashed_password="hashed_pwd",
        full_name="New Google User",
    )
    user_repo.create.return_value = created_user

    oauth_provider = AsyncMock()
    oauth_provider.verify_google_token.return_value = {
        "email": "newuser@example.com",
        "name": "New Google User",
        "sub": "sub-123",
    }

    use_case = GoogleLoginUseCase(
        user_repo=user_repo,
        oauth_provider=oauth_provider,
        secret_key="secret",
        algorithm="HS256",
    )

    user, token = await use_case.execute_id_token("id_token_123")
    assert user.email == "newuser@example.com"
    assert bool(token) is True
    user_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_google_login_existing_active_user():
    existing_user = User(
        id=10,
        email="existing@example.com",
        hashed_password="hashed_pwd",
        full_name="Existing User",
        is_active=True,
    )
    user_repo = AsyncMock()
    user_repo.get_by_email.return_value = existing_user

    oauth_provider = AsyncMock()
    oauth_provider.exchange_code.return_value = {
        "email": "existing@example.com",
        "name": "Existing User",
        "sub": "sub-123",
        "access_token": "token",
    }

    use_case = GoogleLoginUseCase(
        user_repo=user_repo,
        oauth_provider=oauth_provider,
        secret_key="secret",
        algorithm="HS256",
    )

    user, token = await use_case.execute_code("auth_code")
    assert user.id == 10
    assert user.email == "existing@example.com"
    user_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_google_login_deactivated_user_blocked():
    inactive_user = User(
        id=10,
        email="banned@example.com",
        hashed_password="pwd",
        is_active=False,
    )
    user_repo = AsyncMock()
    user_repo.get_by_email.return_value = inactive_user

    oauth_provider = AsyncMock()
    oauth_provider.verify_google_token.return_value = {
        "email": "banned@example.com",
    }

    use_case = GoogleLoginUseCase(
        user_repo=user_repo,
        oauth_provider=oauth_provider,
        secret_key="secret",
        algorithm="HS256",
    )

    with pytest.raises(GoogleAuthFailedError, match="inactive"):
        await use_case.execute_id_token("token_xyz")
