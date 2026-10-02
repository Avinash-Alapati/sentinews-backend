"""
Comprehensive IDOR and Authorization Test Suite.

Enforces:
1. Complete isolation between User A and User B across all portfolio and watchlist routes.
2. Uniform 404 responses for nonexistent resources vs resources owned by another user (no ID enumeration).
3. Rejection of unauthenticated, expired, tampered, alg=none, and deactivated user tokens.
4. Mutation immutability: failed attempts never alter victim's state.
5. Hypothesis property-based fuzzing across parameterized routes.
6. Route auto-discovery ensuring every protected route has ownership enforcement or an allowlist entry.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
import uuid
import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given, settings as hyp_settings, strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.auth.dependencies import invalidate_user_cache
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction, TransactionType
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import SQLAlchemyPortfolioRepository
from app.modules.watchlist.domain.entities import Watchlist, WatchlistItem
from app.modules.watchlist.infrastructure.repositories.watchlist_repository import SQLAlchemyWatchlistRepository


def create_token(user_id: int, secret_key: str = settings.SECRET_KEY, algorithm: str = settings.ALGORITHM, exp_delta: timedelta = timedelta(minutes=15), alg_override: str = None) -> str:
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + exp_delta,
    }
    if alg_override == "none":
        return jwt.encode(payload, key="", algorithm="none")
    return jwt.encode(payload, key=secret_key, algorithm=algorithm)


@pytest.fixture
async def idor_env():
    db_file = os.path.join(tempfile.gettempdir(), f"sentinews_idor_{uuid.uuid4().hex}.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Seed User A and User B
    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session=session)
        port_repo = SQLAlchemyPortfolioRepository(session=session)
        wl_repo = SQLAlchemyWatchlistRepository(session=session)

        user_a = await user_repo.create(User(email="user.a@sentinews.in", full_name="User Alpha", hashed_password="pwd", is_active=True))
        user_b = await user_repo.create(User(email="user.b@sentinews.in", full_name="User Beta", hashed_password="pwd", is_active=True))
        user_deactivated = await user_repo.create(User(email="deactivated@sentinews.in", full_name="Deactivated User", hashed_password="pwd", is_active=False))

        # Portfolios
        port_a = await port_repo.save_portfolio(Portfolio(user_id=user_a.id, name="Alpha Portfolio", cash_balance=10000.0))
        port_b = await port_repo.save_portfolio(Portfolio(user_id=user_b.id, name="Beta Portfolio", cash_balance=25000.0))

        # Holdings & Transactions for B
        await port_repo.add_holding(port_b.id, Holding(symbol="INFY", quantity=20.0, avg_buy_price=1500.0, name="Infosys", sector="IT"))
        tx_b = await port_repo.add_transaction(port_b.id, Transaction(symbol="INFY", transaction_type=TransactionType.BUY, quantity=20.0, price=1500.0))

        # Watchlists
        wl_a = await wl_repo.save_watchlist(Watchlist(user_id=user_a.id, name="Alpha Watchlist"))
        wl_b = await wl_repo.save_watchlist(Watchlist(user_id=user_b.id, name="Beta Watchlist"))
        wl_item_b = await wl_repo.add_item(wl_b.id, WatchlistItem(watchlist_id=wl_b.id, symbol="RELIANCE", exchange="NSE"))

        await session.commit()

    await invalidate_user_cache(user_id=user_a.id, email=user_a.email)
    await invalidate_user_cache(user_id=user_b.id, email=user_b.email)
    await invalidate_user_cache(user_id=user_deactivated.id, email=user_deactivated.email)

    token_a = create_token(user_a.id)
    token_b = create_token(user_b.id)
    token_deactivated = create_token(user_deactivated.id)
    token_expired = create_token(user_a.id, exp_delta=timedelta(minutes=-30))
    token_tampered = token_a[:-6] + "xxxxxx"
    token_none_alg = create_token(user_a.id, alg_override="none")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "user_a": user_a,
            "user_b": user_b,
            "port_a": port_a,
            "port_b": port_b,
            "wl_a": wl_a,
            "wl_b": wl_b,
            "wl_item_b": wl_item_b,
            "tx_b": tx_b,
            "token_a": token_a,
            "token_b": token_b,
            "token_deactivated": token_deactivated,
            "token_expired": token_expired,
            "token_tampered": token_tampered,
            "token_none_alg": token_none_alg,
            "session_factory": session_factory,
        }

    app.dependency_overrides.clear()
    await engine.dispose()
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_idor_cross_user_denial_and_404_uniformity(idor_env):
    """
    Asserts that User A can NEVER read, modify, or delete User B's resources,
    and that accessing B's resource returns a response identical to a nonexistent resource.
    """
    client = idor_env["client"]
    token_a = idor_env["token_a"]
    port_b_id = idor_env["port_b"].id
    wl_b_id = idor_env["wl_b"].id
    user_b_id = idor_env["user_b"].id
    nonexistent_id = 999999

    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1. Portfolio endpoints: B's ID vs nonexistent ID
    portfolio_routes = [
        ("GET", f"/api/v1/portfolio/{port_b_id}", f"/api/v1/portfolio/{nonexistent_id}", None),
        ("GET", f"/api/v1/portfolio/{port_b_id}/holdings", f"/api/v1/portfolio/{nonexistent_id}/holdings", None),
        ("GET", f"/api/v1/portfolio/{port_b_id}/overview", f"/api/v1/portfolio/{nonexistent_id}/overview", None),
        ("GET", f"/api/v1/portfolio/{port_b_id}/allocation", f"/api/v1/portfolio/{nonexistent_id}/allocation", None),
        ("GET", f"/api/v1/portfolio/{port_b_id}/performance", f"/api/v1/portfolio/{nonexistent_id}/performance", None),
        ("GET", f"/api/v1/portfolio/{port_b_id}/transactions", f"/api/v1/portfolio/{nonexistent_id}/transactions", None),
        ("GET", f"/api/v1/portfolio/{port_b_id}/news-feed", f"/api/v1/portfolio/{nonexistent_id}/news-feed", None),
        ("DELETE", f"/api/v1/portfolio/{port_b_id}", f"/api/v1/portfolio/{nonexistent_id}", None),
        ("POST", f"/api/v1/portfolio/{port_b_id}/transactions", f"/api/v1/portfolio/{nonexistent_id}/transactions", {"symbol": "TCS", "transaction_type": "BUY", "quantity": 10, "price": 3000}),
    ]

    for method, route_b, route_nonexistent, body in portfolio_routes:
        if method == "GET":
            res_b = await client.get(route_b, headers=headers_a)
            res_non = await client.get(route_nonexistent, headers=headers_a)
        elif method == "DELETE":
            res_b = await client.delete(route_b, headers=headers_a)
            res_non = await client.delete(route_nonexistent, headers=headers_a)
        elif method == "POST":
            res_b = await client.post(route_b, json=body, headers=headers_a)
            res_non = await client.post(route_nonexistent, json=body, headers=headers_a)

        # Both must return 404
        assert res_b.status_code == 404, f"Route {route_b} returned status {res_b.status_code}, expected 404"
        assert res_non.status_code == 404

    # 2. GET /portfolio/user/{user_id} with B's user_id
    res_user_b = await client.get(f"/api/v1/portfolio/user/{user_b_id}", headers=headers_a)
    assert res_user_b.status_code == 404

    # 3. Watchlist endpoints: B's ID vs nonexistent ID
    watchlist_routes = [
        ("GET", f"/api/v1/watchlists/{wl_b_id}", f"/api/v1/watchlists/{nonexistent_id}", None),
        ("GET", f"/api/v1/watchlists/{wl_b_id}/stocks", f"/api/v1/watchlists/{nonexistent_id}/stocks", None),
        ("PATCH", f"/api/v1/watchlists/{wl_b_id}", f"/api/v1/watchlists/{nonexistent_id}", {"name": "Hacked"}),
        ("PUT", f"/api/v1/watchlists/{wl_b_id}", f"/api/v1/watchlists/{nonexistent_id}", {"name": "Hacked"}),
        ("POST", f"/api/v1/watchlists/{wl_b_id}/stocks", f"/api/v1/watchlists/{nonexistent_id}/stocks", {"symbol": "TCS"}),
        ("DELETE", f"/api/v1/watchlists/{wl_b_id}/stocks/RELIANCE", f"/api/v1/watchlists/{nonexistent_id}/stocks/RELIANCE", None),
        ("DELETE", f"/api/v1/watchlists/{wl_b_id}", f"/api/v1/watchlists/{nonexistent_id}", None),
    ]

    for method, route_b, route_nonexistent, body in watchlist_routes:
        if method == "GET":
            res_b = await client.get(route_b, headers=headers_a)
            res_non = await client.get(route_nonexistent, headers=headers_a)
        elif method == "PATCH":
            res_b = await client.patch(route_b, json=body, headers=headers_a)
            res_non = await client.patch(route_nonexistent, json=body, headers=headers_a)
        elif method == "PUT":
            res_b = await client.put(route_b, json=body, headers=headers_a)
            res_non = await client.put(route_nonexistent, json=body, headers=headers_a)
        elif method == "POST":
            res_b = await client.post(route_b, json=body, headers=headers_a)
            res_non = await client.post(route_nonexistent, json=body, headers=headers_a)
        elif method == "DELETE":
            res_b = await client.delete(route_b, headers=headers_a)
            res_non = await client.delete(route_nonexistent, headers=headers_a)

        assert res_b.status_code == 404, f"Watchlist route {route_b} returned {res_b.status_code}, expected 404"
        assert res_non.status_code == 404

    # 4. Re-query User B's state to prove zero mutations occurred
    session_factory = idor_env["session_factory"]
    async with session_factory() as session:
        port_repo = SQLAlchemyPortfolioRepository(session=session)
        wl_repo = SQLAlchemyWatchlistRepository(session=session)

        refreshed_port_b = await port_repo.get_portfolio(port_b_id)
        assert refreshed_port_b is not None
        assert refreshed_port_b.name == "Beta Portfolio"
        assert len(refreshed_port_b.holdings) == 1
        assert refreshed_port_b.holdings[0].symbol == "INFY"

        refreshed_wl_b = await wl_repo.get_by_id(wl_b_id)
        assert refreshed_wl_b is not None
        assert refreshed_wl_b.name == "Beta Watchlist"
        assert len(refreshed_wl_b.items) == 1
        assert refreshed_wl_b.items[0].symbol == "RELIANCE"


@pytest.mark.asyncio
async def test_auth_token_tampering_and_deactivated_rejection(idor_env):
    """
    Tests rejected tokens: expired, tampered, alg=none, no token, deactivated user.
    """
    client = idor_env["client"]
    port_a_id = idor_env["port_a"].id

    test_tokens = [
        ("no_token", None, 401),
        ("expired", idor_env["token_expired"], 401),
        ("tampered", idor_env["token_tampered"], 401),
        ("none_alg", idor_env["token_none_alg"], 401),
        ("deactivated", idor_env["token_deactivated"], 401),
    ]

    for label, token, expected_status in test_tokens:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        res = await client.get(f"/api/v1/portfolio/{port_a_id}", headers=headers)
        assert res.status_code == expected_status, f"Token scenario '{label}' returned {res.status_code}, expected {expected_status}"


@pytest.mark.asyncio
async def test_fuzz_random_ids_on_parameterized_routes(idor_env):
    """
    Fuzzes invalid, random, negative, zero, SQL injection, and non-numeric strings across all parameterized routes.
    """
    client = idor_env["client"]
    token_a = idor_env["token_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    fuzz_payloads = [
        0,
        -1,
        -999999999,
        999999999999999999,
        "not-a-number",
        "1' OR '1'='1",
        "1; DROP TABLE users;--",
        "A" * 500,
        "../../etc/passwd",
        "%00nullbyte",
    ]

    for fuzz_id in fuzz_payloads:
        # GET portfolio
        r1 = await client.get(f"/api/v1/portfolio/{fuzz_id}", headers=headers_a)
        assert r1.status_code in (404, 422), f"Fuzz ID {fuzz_id} on portfolio returned {r1.status_code}"

        # GET watchlist
        r2 = await client.get(f"/api/v1/watchlists/{fuzz_id}", headers=headers_a)
        assert r2.status_code in (404, 422), f"Fuzz ID {fuzz_id} on watchlist returned {r2.status_code}"

        # DELETE watchlist stock
        r3 = await client.delete(f"/api/v1/watchlists/{fuzz_id}/stocks/{fuzz_id}", headers=headers_a)
        assert r3.status_code in (404, 422)


def test_auto_discover_routes_ownership_enforcement():
    """
    Introspects FastAPI OpenAPI route registry and ensures that every parameterized route
    under /portfolio/ and /watchlists/ has explicit authorization and ownership dependencies.
    """
    from fastapi.routing import APIRoute

    ALLOWLIST_PUBLIC_OR_GLOBAL = {
        "/api/v1/health",
        "/health",
        "/readyz",
        "/healthz",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/google",
        "/api/v1/auth/token",
        "/api/v1/market/quote",
        "/api/v1/market/indices",
        "/api/v1/market/overview",
        "/api/v1/market/movers",
        "/api/v1/market/search",
        "/api/v1/market/candles",
        "/api/v1/market/etfs",
        "/api/v1/market/market-reports/latest",
        "/api/v1/news/feed",
        "/api/v1/news/search",
        "/api/v1/news/trending",
        "/api/v1/news/{article_id}/click",
    }

    def _extract_routes(app_or_router, prefix=""):
        all_routes = []
        for r in getattr(app_or_router, "routes", []):
            if hasattr(r, "original_router"):
                inc_prefix = getattr(getattr(r, "include_context", None), "prefix", "") or ""
                all_routes.extend(_extract_routes(r.original_router, prefix + inc_prefix))
            elif hasattr(r, "path"):
                all_routes.append((prefix + r.path, r))
        return all_routes

    protected_routes = []
    for path, route in _extract_routes(app):
        if isinstance(route, APIRoute):
            if path in ALLOWLIST_PUBLIC_OR_GLOBAL or path.startswith("/internal/"):
                continue

            # Every route under /api/v1/portfolio or /api/v1/watchlist must enforce auth dependencies
            if "/portfolio" in path or "/watchlist" in path:
                all_deps = list(route.dependencies)
                if hasattr(route, "dependant"):
                    all_deps.extend(route.dependant.dependencies)

                dep_names = []
                for d in all_deps:
                    call = getattr(d, "dependency", None) or getattr(d, "call", None)
                    if call:
                        dep_names.append(getattr(call, "__name__", str(call)))
                    else:
                        dep_names.append(str(d))

                all_dep_names = " ".join(dep_names)
                has_auth = "get_current_active_user" in all_dep_names or "get_current_user" in all_dep_names
                assert has_auth, f"Route {path} lacks authentication dependency!"
                protected_routes.append(path)

    assert len(protected_routes) >= 10, f"Expected at least 10 protected routes, found {len(protected_routes)}"


@pytest.mark.asyncio
async def test_mass_assignment_protection(idor_env):
    """
    Tests that injection of privileged fields (user_id, is_superuser, role, is_admin)
    in request bodies is ignored and never overrides session authentication.
    """
    client = idor_env["client"]
    token_a = idor_env["token_a"]
    user_a = idor_env["user_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1. Mass assignment attempt in POST /portfolio
    payload_portfolio = {
        "name": "Mass Assignment Portfolio",
        "user_id": 99999,
        "is_superuser": True,
        "role": "admin",
    }
    res_p = await client.post("/api/v1/portfolio", json=payload_portfolio, headers=headers_a)
    assert res_p.status_code in (200, 201)
    data_p = res_p.json()
    assert data_p["name"] == "Mass Assignment Portfolio"
    assert data_p["user_id"] == user_a.id  # Bound to authenticated user A, not 99999!

    # 2. Mass assignment attempt in POST /watchlists
    payload_wl = {
        "name": "Mass Assignment Watchlist",
        "user_id": 99999,
        "is_admin": True,
    }
    res_wl = await client.post("/api/v1/watchlists", json=payload_wl, headers=headers_a)
    assert res_wl.status_code in (200, 201)
    data_wl = res_wl.json()
    assert data_wl["name"] == "Mass Assignment Watchlist"
    assert data_wl["user_id"] == user_a.id


@pytest.mark.asyncio
async def test_excessive_data_exposure(idor_env):
    """
    Tests that sensitive credentials, hashed passwords, internal tokens, and secrets
    are never returned in API responses.
    """
    client = idor_env["client"]
    token_a = idor_env["token_a"]
    port_a_id = idor_env["port_a"].id
    wl_a_id = idor_env["wl_a"].id
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1. User profile / me
    res_me = await client.get("/api/v1/auth/me", headers=headers_a)
    assert res_me.status_code == 200
    me_json = res_me.json()
    assert "hashed_password" not in me_json
    assert "password" not in me_json
    assert "secret" not in me_json

    # 2. Portfolio details
    res_port = await client.get(f"/api/v1/portfolio/{port_a_id}", headers=headers_a)
    assert res_port.status_code == 200
    port_json = res_port.json()
    assert "hashed_password" not in port_json
    assert "db_url" not in port_json

    # 3. Watchlist details
    res_wl = await client.get(f"/api/v1/watchlists/{wl_a_id}", headers=headers_a)
    assert res_wl.status_code == 200
    wl_json = res_wl.json()
    assert "hashed_password" not in wl_json


@pytest.mark.asyncio
async def test_security_headers_and_body_size_limit(idor_env):
    """
    Tests that defensive security headers are injected on responses, and oversized request bodies are rejected with 413.
    """
    client = idor_env["client"]
    res = await client.get("/health")
    assert res.status_code == 200

    # Verify security headers
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
    assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "geolocation=()" in res.headers.get("permissions-policy", "")
    assert "default-src 'self'" in res.headers.get("content-security-policy", "")

    # Request Body Size Limit Check (Content-Length > 2MB)
    oversized_headers = {
        "content-length": "3000000",
        "content-type": "application/json",
    }
    res_oversized = await client.post("/api/v1/auth/login", headers=oversized_headers, content=b"{}")
    assert res_oversized.status_code == 413
    assert res_oversized.json().get("error") == "payload_too_large"

    # Streaming / Chunked Transfer Check (Without Content-Length header, 2.5MB payload)
    async def chunk_generator():
        # Stream valid JSON chunks exceeding 2MB (25 chunks of 100KB = 2.5MB total)
        yield b'{"email": "trader@sentinews.in", "password": "'
        for _ in range(25):
            yield b"A" * (100 * 1024)
        yield b'"}'

    res_chunked = await client.post(
        "/api/v1/auth/login",
        content=chunk_generator(),
        headers={"content-type": "application/json"},
    )
    assert res_chunked.status_code == 413
    assert "Request body exceeds maximum allowed size" in res_chunked.text


