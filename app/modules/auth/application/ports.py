"""
Application Ports for Authentication module.
"""

from typing import Any, Dict, Optional, Protocol
from app.modules.auth.domain.entities import User


class UserRepository(Protocol):
    """
    Interface for user persistence and retrieval.
    """
    async def get_by_id(self, user_id: int) -> Optional[User]:
        """Loads a user by ID."""
        ...

    async def get_by_email(self, email: str) -> Optional[User]:
        """Loads a user by normalized email address."""
        ...

    async def create(self, user: User) -> User:
        """Persists a new user."""
        ...

    async def update(self, user: User) -> User:
        """Updates user details."""
        ...


class OAuthProvider(Protocol):
    """
    Interface for Google OAuth identity verification and authorization code flow.
    """
    def get_authorization_url(self, state: str, redirect_uri: Optional[str] = None) -> str:
        """Constructs Google OAuth 2.0 consent screen redirect URL."""
        ...

    async def exchange_code(self, code: str, redirect_uri: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Exchanges authorization code for tokens and extracts verified profile info."""
        ...

    async def verify_google_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verifies a Google OAuth ID token."""
        ...

    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fetches user profile information from Google userinfo endpoint."""
        ...
