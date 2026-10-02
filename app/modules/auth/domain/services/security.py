"""
Domain service for Password Hashing and JWT Token operations.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import asyncio
import bcrypt
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError as PyJWTInvalidTokenError, PyJWTError

# JWT configuration defaults
DEFAULT_SECRET_KEY = "sentinews_super_secret_jwt_key_development_only"
DEFAULT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours


class InvalidTokenError(Exception):
    """Raised when token is invalid, malformed, or signature is tampered."""
    pass


class TokenExpiredError(Exception):
    """Raised when token has passed its expiration timestamp."""
    pass


# Password hashing configuration using direct bcrypt implementation
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain-text password against a hashed password."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Generates a secure hash of the password."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# Alias for backward compatibility and uniform nomenclature
hash_password = get_password_hash


async def async_verify_password(plain_password: str, hashed_password: str) -> bool:
    """Asynchronously verifies password in a threadpool worker to avoid blocking the event loop."""
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)


async def async_get_password_hash(password: str) -> str:
    """Asynchronously generates password hash in a threadpool worker to avoid blocking the event loop."""
    return await asyncio.to_thread(get_password_hash, password)


async_hash_password = async_get_password_hash


def create_access_token(
    subject: str,
    secret_key: str = DEFAULT_SECRET_KEY,
    algorithm: str = DEFAULT_ALGORITHM,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Encodes a JWT access token containing subject identifier and expiration.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta if expires_delta is not None else timedelta(minutes=DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode: Dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        to_encode.update(extra_claims)

    encoded_jwt = jwt.encode(to_encode, secret_key, algorithm=algorithm)
    return encoded_jwt


def decode_access_token(
    token: str,
    secret_key: str = DEFAULT_SECRET_KEY,
    algorithm: str = DEFAULT_ALGORITHM,
) -> Dict[str, Any]:
    """
    Decodes and verifies a JWT access token.

    Raises:
        TokenExpiredError: If the token has expired.
        InvalidTokenError: If signature is tampered or payload is invalid.
    """
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
            options={"verify_exp": True, "verify_sub": True},
        )
        return payload
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except (PyJWTInvalidTokenError, PyJWTError) as exc:
        raise InvalidTokenError("Invalid token or signature verification failed") from exc
