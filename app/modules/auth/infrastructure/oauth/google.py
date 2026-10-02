"""
Google OAuth 2.0 Provider implementation.

Supports:
1. Google OAuth2 Authorization Code redirect & code exchange flows.
2. Direct Google ID Token verification (One-Tap, SPA, NextAuth, Mobile).
3. Google UserInfo endpoint resolution.
"""

import logging
import os
import urllib.parse
from typing import Any, Dict, Optional
from app.core.config import settings
from app.infrastructure.observability.http_tracer import create_traced_async_client
from app.modules.auth.application.ports import OAuthProvider

logger = logging.getLogger(__name__)


class GoogleOAuthNotConfiguredError(Exception):
    """Raised when Google OAuth is invoked but required credentials are missing."""
    pass


class GoogleOAuthError(Exception):
    """Raised when Google OAuth token exchange or verification fails."""
    pass


class GoogleOAuthProvider(OAuthProvider):
    """
    Production-grade Google OAuth 2.0 Provider.
    """

    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    DEFAULT_SCOPES = ["openid", "email", "profile"]

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        self.client_id = (client_id if client_id is not None else settings.GOOGLE_CLIENT_ID).strip()
        self.client_secret = (client_secret if client_secret is not None else settings.GOOGLE_CLIENT_SECRET).strip()
        self.redirect_uri = (redirect_uri if redirect_uri is not None else settings.GOOGLE_REDIRECT_URI).strip()

    @property
    def is_configured(self) -> bool:
        """Returns True if at least client_id is configured (enough for ID token verification)."""
        return bool(self.client_id)

    @property
    def is_full_oauth_configured(self) -> bool:
        """Returns True if client_id and client_secret are configured (required for code exchange)."""
        return bool(self.client_id and self.client_secret)

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: Optional[str] = None,
        prompt: str = "select_account",
    ) -> str:
        """
        Constructs the Google OAuth 2.0 authorization URL for user redirect.

        Raises:
            GoogleOAuthNotConfiguredError: If GOOGLE_CLIENT_ID is not configured.
        """
        if not self.is_configured:
            raise GoogleOAuthNotConfiguredError(
                "Google OAuth is not configured on this server (GOOGLE_CLIENT_ID unset)"
            )

        effective_redirect_uri = redirect_uri or self.redirect_uri
        params = {
            "client_id": self.client_id,
            "redirect_uri": effective_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.DEFAULT_SCOPES),
            "state": state,
            "access_type": "offline",
            "prompt": prompt,
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Exchanges an authorization code for Google access & ID tokens and resolves user profile.

        Raises:
            GoogleOAuthNotConfiguredError: If client ID or Secret is not configured.
            GoogleOAuthError: If Google rejects the code or returns an error.
        """
        if not self.is_full_oauth_configured:
            raise GoogleOAuthNotConfiguredError(
                "Google OAuth code exchange requires both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET"
            )

        effective_redirect_uri = redirect_uri or self.redirect_uri
        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": effective_redirect_uri,
            "grant_type": "authorization_code",
        }

        try:
            async with create_traced_async_client(provider="google_oauth", timeout=10.0) as client:
                res = await client.post(self.TOKEN_URL, data=payload)
                if res.status_code != 200:
                    error_detail = res.text
                    try:
                        error_json = res.json()
                        error_detail = error_json.get("error_description") or error_json.get("error") or error_detail
                    except Exception:
                        pass
                    logger.warning("Google token exchange failed: %s (status: %d)", error_detail, res.status_code)
                    raise GoogleOAuthError(f"Google token exchange failed: {error_detail}")

                token_data = res.json()
                id_token = token_data.get("id_token")
                access_token = token_data.get("access_token")

                # Retrieve user profile from id_token or userinfo
                user_info: Dict[str, Any] = {}
                if id_token:
                    verified_info = await self.verify_google_token(id_token)
                    if verified_info:
                        user_info = verified_info

                if not user_info.get("email") and access_token:
                    fetched_info = await self.get_user_info(access_token)
                    if fetched_info:
                        user_info = fetched_info

                if not user_info.get("email"):
                    raise GoogleOAuthError("Could not retrieve verified email from Google OAuth response")

                return {
                    "email": user_info["email"],
                    "name": user_info.get("name"),
                    "sub": user_info.get("sub"),
                    "picture": user_info.get("picture"),
                    "email_verified": user_info.get("email_verified", True),
                    "id_token": id_token,
                    "access_token": access_token,
                }
        except (GoogleOAuthNotConfiguredError, GoogleOAuthError):
            raise
        except Exception as exc:
            logger.error("Error communicating with Google OAuth token endpoint: %s", str(exc), exc_info=True)
            raise GoogleOAuthError(f"Network or connection error during Google OAuth exchange: {str(exc)}") from exc

    async def verify_google_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verifies a Google ID token using Google's public tokeninfo endpoint.

        Raises:
            GoogleOAuthNotConfiguredError: If GOOGLE_CLIENT_ID is unset.
        """
        if not self.is_configured:
            raise GoogleOAuthNotConfiguredError(
                "Google OAuth is not configured on this server (GOOGLE_CLIENT_ID unset)"
            )

        url = f"{self.TOKENINFO_URL}?id_token={token}"
        try:
            async with create_traced_async_client(provider="google_oauth", timeout=10.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    # Verify audience matches our configured client ID
                    if data.get("aud") == self.client_id:
                        return {
                            "email": data.get("email"),
                            "name": data.get("name"),
                            "sub": data.get("sub"),
                            "picture": data.get("picture"),
                            "email_verified": str(data.get("email_verified", "")).lower() in ("true", "1"),
                        }
                    logger.warning("Google ID token audience mismatch. Expected: %s, Got: %s", self.client_id, data.get("aud"))
                else:
                    logger.warning("Google tokeninfo returned status %d: %s", res.status_code, res.text)
                return None
        except Exception as exc:
            logger.error("Error verifying Google token: %s", str(exc), exc_info=True)
            return None

    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Fetches user profile information from Google's userinfo endpoint.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with create_traced_async_client(provider="google_oauth", timeout=10.0) as client:
                res = await client.get(self.USERINFO_URL, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "email": data.get("email"),
                        "name": data.get("name"),
                        "sub": data.get("sub"),
                        "picture": data.get("picture"),
                        "email_verified": bool(data.get("email_verified", True)),
                    }
                return None
        except Exception as exc:
            logger.error("Error fetching Google userinfo: %s", str(exc), exc_info=True)
            return None
