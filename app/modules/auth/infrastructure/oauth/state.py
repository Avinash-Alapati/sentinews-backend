"""
OAuth CSRF State token generation and validation.

Secures OAuth redirect and callback flows against Cross-Site Request Forgery (CSRF).
"""

import json
import time
import base64
import hmac
import hashlib
from typing import Any, Dict, Optional


class InvalidOAuthStateError(Exception):
    """Raised when an OAuth CSRF state parameter is missing, expired, or tampered."""
    pass


def generate_oauth_state(
    secret_key: str,
    provider: str = "google",
    return_url: Optional[str] = None,
    expires_in_seconds: int = 600,
) -> str:
    """
    Generates a cryptographically signed, timestamped state token.

    Args:
        secret_key: Application secret key used to sign the state.
        provider: Expected OAuth provider (e.g. 'google').
        return_url: Optional post-login redirect URL.
        expires_in_seconds: State validity duration (default 10 minutes).

    Returns:
        URL-safe signed state token string.
    """
    payload = {
        "provider": provider,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in_seconds,
    }
    if return_url:
        payload["return_url"] = return_url

    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_json).decode("utf-8").rstrip("=")

    signature = hmac.new(
        key=secret_key.encode("utf-8"),
        msg=payload_b64.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return f"{payload_b64}.{signature}"


def verify_oauth_state(
    state: str,
    secret_key: str,
    expected_provider: str = "google",
) -> Dict[str, Any]:
    """
    Verifies and unpacks an OAuth state token.

    Args:
        state: The state parameter received in the OAuth callback.
        secret_key: Application secret key.
        expected_provider: Expected provider string.

    Returns:
        Decoded payload dictionary containing optional return_url.

    Raises:
        InvalidOAuthStateError: If the state is tampered, expired, or malformed.
    """
    if not state or "." not in state:
        raise InvalidOAuthStateError("OAuth state parameter is missing or malformed")

    parts = state.split(".")
    if len(parts) != 2:
        raise InvalidOAuthStateError("Invalid state format")

    payload_b64, signature = parts[0], parts[1]

    expected_signature = hmac.new(
        key=secret_key.encode("utf-8"),
        msg=payload_b64.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidOAuthStateError("State signature mismatch or potential CSRF attempt")

    try:
        # Pad base64 string if necessary
        padded_b64 = payload_b64 + "=" * (-len(payload_b64) % 4)
        raw_json = base64.urlsafe_b64decode(padded_b64.encode("utf-8")).decode("utf-8")
        payload = json.loads(raw_json)
    except Exception as exc:
        raise InvalidOAuthStateError("Failed to decode state payload") from exc

    # Check expiration
    exp = payload.get("exp", 0)
    if time.time() > exp:
        raise InvalidOAuthStateError("OAuth state has expired. Please initiate login again.")

    # Check provider
    provider = payload.get("provider")
    if provider != expected_provider:
        raise InvalidOAuthStateError(f"OAuth provider mismatch: expected {expected_provider}, got {provider}")

    return payload
