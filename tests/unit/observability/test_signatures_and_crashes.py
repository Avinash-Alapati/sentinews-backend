"""
Unit tests for Crash Signature Engine and PII Scrubbing.
"""

from app.infrastructure.observability.logging_handler import scrub_sensitive_data
from app.infrastructure.observability.signatures import CrashSignatureEngine


def test_signature_computation_and_determinism():
    engine = CrashSignatureEngine()

    try:
        raise ValueError("Testing error signature")
    except ValueError as exc:
        sig1, loc1, prom1 = engine.compute_signature(exc, endpoint="/api/v1/test")
        sig2, loc2, prom2 = engine.compute_signature(exc, endpoint="/api/v1/test")

        assert len(sig1) == 8
        assert sig1 == sig2
        assert "test_signatures_and_crashes.py" in loc1 or "unknown" in loc1
        assert prom1 == sig1


def test_cardinality_ceiling_caps_at_200():
    engine = CrashSignatureEngine()
    engine.MAX_SIGNATURES = 200

    # Generate 250 distinct crash exceptions
    for i in range(250):
        try:
            # Dynamically raise distinct exception types or simulate distinct locations
            exec_globals = {}
            exec_locals = {}
            exec(f"class DynamicError{i}(Exception): pass\nraise DynamicError{i}('Crash {i}')", exec_globals, exec_locals)
        except Exception as exc:
            sig, loc, prom_sig = engine.compute_signature(exc)
            engine.record_crash(exc)

    records = engine.get_all_records()
    assert len(records) <= 200

    # Ensure overflow signatures are bucketed as 'other'
    try:
        class OverflowErrorTest(Exception):
            pass
        raise OverflowErrorTest("Overflow test")
    except Exception as exc:
        sig, loc, prom_sig = engine.compute_signature(exc)
        assert prom_sig == "other"


def test_scrub_sensitive_data():
    raw_text = (
        "User user@example.com logged in with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz "
        "and password='SuperSecretPassword123' api_key: 'sk_live_123456789' secret = \"my_jwt_secret\""
    )

    scrubbed = scrub_sensitive_data(raw_text)

    assert "user@example.com" not in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz" not in scrubbed
    assert "[REDACTED_TOKEN]" in scrubbed
    assert "SuperSecretPassword123" not in scrubbed
    assert "[REDACTED_PASSWORD]" in scrubbed
    assert "sk_live_123456789" not in scrubbed
    assert "[REDACTED_KEY]" in scrubbed
    assert "my_jwt_secret" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed
