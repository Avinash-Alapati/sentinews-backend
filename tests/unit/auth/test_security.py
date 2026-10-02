"""
Unit tests for Authentication Security Domain Service (no DB, no network).
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.modules.auth.domain.services.security import (
    InvalidTokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)


def test_password_hashing_round_trip():
    raw_password = "super_secure_password_123!"
    hashed = get_password_hash(raw_password)

    assert hashed != raw_password
    assert verify_password(raw_password, hashed) is True
    assert verify_password("wrong_password", hashed) is False


def test_jwt_encode_decode_round_trip():
    secret_key = "test_secret_key_12345"
    token = create_access_token(
        subject="42",
        secret_key=secret_key,
        expires_delta=timedelta(minutes=15),
        extra_claims={"email": "investor@example.com"},
    )

    payload = decode_access_token(token, secret_key=secret_key)
    assert payload["sub"] == "42"
    assert payload["email"] == "investor@example.com"
    assert "exp" in payload
    assert "iat" in payload


def test_jwt_tampered_signature_raises_invalid_token():
    secret_key = "test_secret_key_12345"
    token = create_access_token(subject="42", secret_key=secret_key)

    # Tamper with token payload / signature
    parts = token.split(".")
    tampered_token = f"{parts[0]}.{parts[1]}.tampered_signature"

    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered_token, secret_key=secret_key)

    # Wrong secret key
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret_key="completely_different_key")


def test_jwt_expired_raises_token_expired_error():
    secret_key = "test_secret_key_12345"
    # Create token that expired 10 minutes ago
    expired_delta = timedelta(minutes=-10)
    token = create_access_token(
        subject="42",
        secret_key=secret_key,
        expires_delta=expired_delta,
    )

    with pytest.raises(TokenExpiredError):
        decode_access_token(token, secret_key=secret_key)
