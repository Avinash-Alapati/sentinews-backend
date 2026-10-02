"""
Unit tests verifying fail-fast startup validation for production configurations in Settings.
"""

import pytest
from pydantic import ValidationError
from app.core.config import Settings

BASE_PROD_CONFIG = {
    "ENVIRONMENT": "production",
    "SECRET_KEY": "super_strong_production_jwt_signing_key_32_chars",
    "INTERNAL_API_SECRET": "production_internal_secret_1234567890123456",
    "INTERNAL_METRICS_TOKEN": "production_metrics_token_1234567890123456",
    "REFRESH_TOKEN_ROTATION_GRACE_SECONDS": 10,
    "CORS_ORIGINS": ["https://app.sentinews.in"],
    "DATABASE_URL": "postgresql+asyncpg://sentinews_user:StrongPass123@prod-db.internal:5432/sentinews",
    "SYNC_DATABASE_URL": "postgresql://sentinews_user:StrongPass123@prod-db.internal:5432/sentinews",
    "REDIS_URL": "rediss://prod-redis.internal:6379/0",
    "LOG_LEVEL": "INFO",
    "DEBUG": False,
}


def test_production_rejects_default_secret_key():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "SECRET_KEY": "your_super_secret_jwt_key_here_change_in_production"})
    assert "SECRET_KEY" in str(exc_info.value)


def test_production_rejects_short_secret_key():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "SECRET_KEY": "short_key_123"})
    assert "SECRET_KEY" in str(exc_info.value)


def test_production_rejects_default_metrics_token():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "INTERNAL_METRICS_TOKEN": "sentinews_metrics_secret_token_change_in_production"})
    assert "INTERNAL_METRICS_TOKEN" in str(exc_info.value)


def test_production_rejects_default_internal_api_secret():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "INTERNAL_API_SECRET": "sentinews_internal_api_secret_change_in_production"})
    assert "INTERNAL_API_SECRET" in str(exc_info.value)


def test_production_rejects_identical_internal_and_metrics_secrets():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{
            **BASE_PROD_CONFIG,
            "INTERNAL_API_SECRET": "identical_secret_value_1234567890123456",
            "INTERNAL_METRICS_TOKEN": "identical_secret_value_1234567890123456",
        })
    assert "INTERNAL_API_SECRET and INTERNAL_METRICS_TOKEN must be distinct" in str(exc_info.value)


def test_production_rejects_cors_wildcard():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "CORS_ORIGINS": ["*"]})
    assert "CORS_ORIGINS" in str(exc_info.value)


def test_production_rejects_cors_localhost():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "CORS_ORIGINS": ["http://localhost:3000", "https://app.sentinews.in"]})
    assert "CORS_ORIGINS" in str(exc_info.value)


def test_production_rejects_default_database_url():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/sentinews"})
    assert "DATABASE_URL" in str(exc_info.value)


def test_production_rejects_localhost_redis_url():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "REDIS_URL": "redis://localhost:6379/0", "REDIS_ENABLED": True})
    assert "REDIS_URL" in str(exc_info.value)


def test_production_rejects_debug_mode():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**{**BASE_PROD_CONFIG, "LOG_LEVEL": "DEBUG", "DEBUG": True})
    assert "DEBUG" in str(exc_info.value)


def test_production_accepts_valid_production_settings():
    valid_settings = Settings(**BASE_PROD_CONFIG)
    assert valid_settings.ENVIRONMENT == "production"
    assert valid_settings.SECRET_KEY == "super_strong_production_jwt_signing_key_32_chars"
    assert valid_settings.REFRESH_TOKEN_ROTATION_GRACE_SECONDS == 10
    assert valid_settings.INTERNAL_API_SECRET != valid_settings.INTERNAL_METRICS_TOKEN
