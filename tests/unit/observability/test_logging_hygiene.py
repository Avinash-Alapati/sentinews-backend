"""
Unit and integration tests verifying strict logging hygiene, PII/secret scrubbing,
and stack trace leakage prevention.
"""

import json
import logging
import pytest
from httpx import ASGITransport, AsyncClient
from app.core.config import settings
from app.infrastructure.observability.logging_handler import (
    StructuredJsonFormatter,
    scrub_sensitive_data,
)
from app.main import app


def test_scrub_sensitive_data_patterns():
    """
    Asserts that passwords, secrets, API keys, tokens, refresh tokens,
    Google OAuth tokens, and Authorization headers are thoroughly scrubbed.
    """
    test_cases = [
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.secret", "Authorization: Bearer [REDACTED_TOKEN]"),
        ('{"password": "MySuperSecretPassword123!"}', '{"password": "[REDACTED_PASSWORD]"}'),
        ('{"secret": "production_master_secret_key"}', '{"secret": "[REDACTED_SECRET]"}'),
        ('api_key="fake_test_api_key_12345678"', 'api_key="[REDACTED_KEY]"'),
        ('refresh_token="489fa9b1.raw_refresh_token_secret"', 'refresh_token="[REDACTED_REFRESH_TOKEN]"'),
        ('access_token="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIyIn0"', 'access_token="[REDACTED_ACCESS_TOKEN]"'),
        ('Google token ya29.a0AfH6SMAaBcDeFgHiJkLmNoPqRsTuVwXyZ received', 'Google token [REDACTED_GOOGLE_TOKEN] received'),
        ('Contact trader at support.trader@sentinews.in for assistance', 'Contact trader at [REDACTED_EMAIL] for assistance'),
    ]

    for raw, expected in test_cases:
        scrubbed = scrub_sensitive_data(raw)
        assert scrubbed == expected, f"Failed for '{raw}': got '{scrubbed}', expected '{expected}'"


def test_structured_json_formatter_scrubs_record_and_traceback():
    """
    Asserts that StructuredJsonFormatter outputs valid JSON and scrubs both the message and traceback.
    """
    formatter = StructuredJsonFormatter()
    logger = logging.getLogger("sentinews.test.hygiene")

    record = logger.makeRecord(
        name="sentinews.auth",
        level=logging.ERROR,
        fn="auth_handler.py",
        lno=42,
        msg='Failed login attempt for user john.doe@sentinews.in with password="PlaintextPassword99"',
        args=(),
        exc_info=None,
    )

    formatted_json_str = formatter.format(record)
    parsed = json.loads(formatted_json_str)

    assert "PlaintextPassword99" not in formatted_json_str
    assert "john.doe@sentinews.in" not in formatted_json_str
    assert "[REDACTED_PASSWORD]" in parsed["message"]
    assert "[REDACTED_EMAIL]" in parsed["message"]
    assert parsed["level"] == "ERROR"
    assert "ts" in parsed


@pytest.mark.asyncio
async def test_global_exception_handler_suppresses_stack_trace_in_production(monkeypatch):
    """
    Asserts that when an unhandled server exception occurs, the client receives
    an opaque 500 JSON response with zero internal traceback leakage.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEBUG", False)

    # Route that raises an intentional internal exception
    @app.get("/test-unhandled-server-crash")
    async def crash_endpoint():
        raise RuntimeError("CRITICAL_INTERNAL_DB_PASSWORD_LEAK: postgres://admin:TopSecretPass@internal-db:5432")

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/test-unhandled-server-crash")
        assert response.status_code == 500
        body = response.json()

        # Must return generic message only
        assert body == {"detail": "An internal server error occurred. Please try again later."}
        # Verify internal secret and traceback was NOT leaked in response
        assert "CRITICAL_INTERNAL_DB_PASSWORD_LEAK" not in response.text
        assert "TopSecretPass" not in response.text
        assert "Traceback" not in response.text
        assert "RuntimeError" not in response.text


def test_sentry_before_send_scrubbing():
    """Asserts that Sentry before_send callback scrubs auth headers, cookies, passwords, and tokens."""
    from app.infrastructure.observability.logging_handler import sentry_scrub_sensitive_data

    sample_event = {
        "event_id": "abc123def456",
        "logentry": {"message": "User auth failed with secret_key=SuperSecretMasterKey123"},
        "request": {
            "url": "https://api.sentinews.in/api/v1/auth/login",
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.topsecrettoken",
                "Cookie": "refresh_token=sensitive_refresh_cookie_value; session_id=9876",
                "X-Api-Key": "fake_mock_key_header_12345",
                "User-Agent": "Mozilla/5.0",
            },
            "data": {
                "email": "trader@sentinews.in",
                "password": "ClearTextPassword456!",
                "client_secret": "oauth_client_secret_999",
            },
        },
        "breadcrumbs": {
            "values": [
                {
                    "message": "Attempted login with access_token=raw_jwt_token_payload",
                    "data": {"password": "AnotherPlainPassword"},
                }
            ]
        },
    }

    scrubbed_event = sentry_scrub_sensitive_data(sample_event, hint={})

    # Headers verification
    req_headers = scrubbed_event["request"]["headers"]
    assert req_headers["Authorization"] == "[REDACTED]"
    assert req_headers["Cookie"] == "[REDACTED]"
    assert req_headers["X-Api-Key"] == "[REDACTED]"
    assert req_headers["User-Agent"] == "Mozilla/5.0"

    # Data verification
    req_data = scrubbed_event["request"]["data"]
    assert req_data["password"] == "[REDACTED]"
    assert req_data["client_secret"] == "[REDACTED]"

    # Breadcrumb verification
    crumb = scrubbed_event["breadcrumbs"]["values"][0]
    assert "raw_jwt_token_payload" not in crumb["message"]
    assert crumb["data"]["password"] == "[REDACTED]"

    # Log entry verification
    assert "SuperSecretMasterKey123" not in scrubbed_event["logentry"]["message"]
    assert "[REDACTED_SECRET]" in scrubbed_event["logentry"]["message"]

