"""
Google Login Use Case.

Handles:
1. Authorization URL generation with secure CSRF state.
2. Callback handling & OAuth2 Authorization Code exchange.
3. ID Token direct verification.
4. User account provisioning / retrieval and JWT generation.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple
import uuid

from app.modules.auth.application.ports import OAuthProvider, UserRepository
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token, get_password_hash
from app.modules.auth.infrastructure.oauth.google import (
    GoogleOAuthError,
    GoogleOAuthNotConfiguredError,
)
from app.modules.auth.infrastructure.oauth.state import (
    InvalidOAuthStateError,
    generate_oauth_state,
    verify_oauth_state,
)

logger = logging.getLogger(__name__)


class GoogleAuthFailedError(Exception):
    """Raised when Google authentication fails or user cannot be authenticated."""
    pass


class GoogleLoginUseCase:
    """
    End-to-End Use Case for Google OAuth 2.0.
    """

    def __init__(
        self,
        user_repo: UserRepository,
        oauth_provider: OAuthProvider,
        secret_key: str,
        algorithm: str,
    ):
        self.user_repo = user_repo
        self.oauth_provider = oauth_provider
        self.secret_key = secret_key
        self.algorithm = algorithm

    def get_authorization_url(
        self,
        redirect_uri: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Generates Google OAuth authorization URL and signed CSRF state.
        """
        state = generate_oauth_state(
            secret_key=self.secret_key,
            provider="google",
            return_url=return_url,
        )
        url = self.oauth_provider.get_authorization_url(state=state, redirect_uri=redirect_uri)
        return {"authorization_url": url, "state": state}

    async def _find_or_create_user(self, profile: Dict[str, Any]) -> User:
        """Helper to find existing user or provision new user account."""
        email = profile.get("email", "").lower().strip()
        if not email:
            raise GoogleAuthFailedError("Google profile did not contain a valid email address")

        user = await self.user_repo.get_by_email(email)

        if user is None:
            # Provision new user with high-entropy randomized password
            random_pwd = str(uuid.uuid4())
            hashed_pwd = await asyncio.to_thread(get_password_hash, random_pwd)
            new_user = User(
                email=email,
                hashed_password=hashed_pwd,
                full_name=profile.get("name"),
                is_active=True,
            )
            user = await self.user_repo.create(new_user)
            logger.info("Provisioned new user account via Google OAuth: %s", email)
        else:
            if not user.is_active:
                raise GoogleAuthFailedError("User account is inactive")
            # Update full_name if missing
            if not user.full_name and profile.get("name"):
                user.full_name = profile["name"]
                await self.user_repo.update(user)

        return user

    def _generate_token(self, user: User) -> str:
        """Generates application JWT access token."""
        return create_access_token(
            subject=str(user.id or user.email),
            secret_key=self.secret_key,
            algorithm=self.algorithm,
            extra_claims={"email": user.email},
        )

    async def execute_id_token(self, id_token: str) -> Tuple[User, str]:
        """
        Authenticates user using a Google ID token.
        """
        token_info = await self.oauth_provider.verify_google_token(id_token)
        if not token_info or not token_info.get("email"):
            raise GoogleAuthFailedError("Google ID token verification failed or token is invalid")

        user = await self._find_or_create_user(token_info)
        access_token = self._generate_token(user)
        return user, access_token

    async def execute_code(
        self,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> Tuple[User, str]:
        """
        Authenticates user by exchanging Google OAuth authorization code.
        """
        try:
            profile = await self.oauth_provider.exchange_code(code=code, redirect_uri=redirect_uri)
        except (GoogleOAuthNotConfiguredError, GoogleOAuthError) as exc:
            raise GoogleAuthFailedError(str(exc)) from exc

        if not profile or not profile.get("email"):
            raise GoogleAuthFailedError("Failed to retrieve user profile from Google OAuth exchange")

        user = await self._find_or_create_user(profile)
        access_token = self._generate_token(user)
        return user, access_token

    async def handle_callback(
        self,
        code: str,
        state: str,
        redirect_uri: Optional[str] = None,
    ) -> Tuple[User, str, Optional[str]]:
        """
        Validates CSRF state, exchanges authorization code, and completes OAuth login.

        Returns:
            Tuple of (User, access_token, return_url)
        """
        try:
            state_data = verify_oauth_state(
                state=state,
                secret_key=self.secret_key,
                expected_provider="google",
            )
        except InvalidOAuthStateError as exc:
            raise GoogleAuthFailedError(f"CSRF validation failed: {str(exc)}") from exc

        return_url = state_data.get("return_url")
        user, access_token = await self.execute_code(code=code, redirect_uri=redirect_uri)
        return user, access_token, return_url

    # Backward compatibility alias
    async def execute(self, id_token: str) -> Tuple[User, str]:
        return await self.execute_id_token(id_token=id_token)
