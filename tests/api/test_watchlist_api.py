"""
API tests for Watchlist endpoints:
- Authentication requirement (401 on unauthenticated access)
- Watchlist CRUD lifecycle (Create, Read list, Read single, Update/Rename, Delete)
- Stock management (Add stock, Remove stock)
- Duplicate stock prevention (409 Conflict)
- User ownership & security isolation (User B cannot access/modify User A's watchlists)
"""

import asyncio
import os
import tempfile
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
async def test_watchlist_app_client():
    db_file = os.path.join(tempfile.gettempdir(), f"sentinews_test_{uuid.uuid4().hex}.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
    )
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_unauthenticated_access_rejected(test_watchlist_app_client: AsyncClient):
    """Ensure all watchlist endpoints reject unauthenticated requests with 401."""
    res_list = await test_watchlist_app_client.get("/api/v1/watchlists")
    assert res_list.status_code == 401

    res_create = await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Tech"})
    assert res_create.status_code == 401

    res_get = await test_watchlist_app_client.get("/api/v1/watchlists/1")
    assert res_get.status_code == 401


@pytest.mark.asyncio
async def test_watchlist_full_lifecycle_and_stock_management(test_watchlist_app_client: AsyncClient):
    """
    Test complete flow:
    1. Register & authenticate User 1
    2. Create watchlists 'IT Companies' and 'Banking'
    3. Add stocks (TCS, INFY) to 'IT Companies'
    4. Verify duplicate prevention on TCS
    5. Read single watchlist with stocks
    6. Rename watchlist to 'Indian IT'
    7. Remove a stock from watchlist
    8. Delete watchlist 'Banking'
    """
    # 1. Register User 1
    reg_res = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "user1@sentinews.in", "password": "User1Password123!", "full_name": "User One"},
    )
    assert reg_res.status_code == 201
    token1 = reg_res.json()["access_token"]
    user1_id = reg_res.json()["user"]["id"]
    headers1 = {"Authorization": f"Bearer {token1}"}

    # 2. Create Watchlist 1: "IT Companies"
    w1_res = await test_watchlist_app_client.post(
        "/api/v1/watchlists",
        json={"name": "IT Companies"},
        headers=headers1,
    )
    assert w1_res.status_code == 201
    w1_data = w1_res.json()
    w1_id = w1_data["id"]
    assert w1_data["name"] == "IT Companies"
    assert w1_data["stock_count"] == 0

    # Create Watchlist 2: "Banking"
    w2_res = await test_watchlist_app_client.post(
        "/api/v1/watchlists",
        json={"name": "Banking"},
        headers=headers1,
    )
    assert w2_res.status_code == 201
    w2_id = w2_res.json()["id"]

    # 3. Add stocks to Watchlist 1
    add_tcs = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{w1_id}/stocks",
        json={"symbol": "TCS", "exchange": "NSE", "notes": "Top IT player"},
        headers=headers1,
    )
    assert add_tcs.status_code == 201
    assert add_tcs.json()["symbol"] == "TCS"
    assert add_tcs.json()["exchange"] == "NSE"

    add_infy = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{w1_id}/stocks",
        json={"symbol": "INFY", "exchange": "NSE"},
        headers=headers1,
    )
    assert add_infy.status_code == 201
    assert add_infy.json()["symbol"] == "INFY"

    # 4. Prevent duplicate stock addition (TCS again)
    dup_tcs = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{w1_id}/stocks",
        json={"symbol": "tcs"},
        headers=headers1,
    )
    assert dup_tcs.status_code == 409
    assert "already present" in dup_tcs.json()["detail"].lower()

    # 5. Get User 1's watchlists (now includes stock details)
    list_res = await test_watchlist_app_client.get("/api/v1/watchlists", headers=headers1)
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert len(list_data) == 2
    # Find IT Companies in list
    it_detail = next(w for w in list_data if w["id"] == w1_id)
    assert it_detail["name"] == "IT Companies"
    assert it_detail["stock_count"] == 2
    assert len(it_detail["stocks"]) == 2
    symbols = [s["symbol"] for s in it_detail["stocks"]]
    assert "TCS" in symbols
    assert "INFY" in symbols

    # 6. Get all user watchlists via GET /user/{user_id} (Summary only - no stocks array)
    get_by_user = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user1_id}", headers=headers1)
    assert get_by_user.status_code == 200
    user_watchlists = get_by_user.json()
    assert isinstance(user_watchlists, list)
    assert len(user_watchlists) == 2
    w1_summary = next(w for w in user_watchlists if w["id"] == w1_id)
    assert w1_summary["name"] == "IT Companies"
    assert w1_summary["stock_count"] == 2
    assert "stocks" not in w1_summary

    # Test single watchlist GET /{watchlist_id} (Detail with stocks)
    single_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/{w1_id}", headers=headers1)
    assert single_res.status_code == 200
    single_data = single_res.json()
    assert isinstance(single_data, dict)
    assert single_data["id"] == w1_id
    assert single_data["name"] == "IT Companies"
    assert single_data["stock_count"] == 2
    assert len(single_data["stocks"]) == 2

    # Test new GET /api/v1/watchlists/{watchlist_id}/stocks endpoint
    stocks_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/{w1_id}/stocks", headers=headers1)
    assert stocks_res.status_code == 200
    stocks_data = stocks_res.json()
    assert isinstance(stocks_data, list)
    assert len(stocks_data) == 2
    symbols = [s["symbol"] for s in stocks_data]
    assert "TCS" in symbols
    assert "INFY" in symbols

    # 7. Rename watchlist
    patch_res = await test_watchlist_app_client.patch(
        f"/api/v1/watchlists/{w1_id}",
        json={"name": "Indian IT Companies"},
        headers=headers1,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Indian IT Companies"

    # 8. Remove stock (INFY)
    del_stock_res = await test_watchlist_app_client.delete(
        f"/api/v1/watchlists/{w1_id}/stocks/INFY",
        headers=headers1,
    )
    assert del_stock_res.status_code == 200

    # Verify stock count via summary and stock items via stocks endpoint
    get_after_del = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user1_id}", headers=headers1)
    w1_after = next(w for w in get_after_del.json() if w["id"] == w1_id)
    assert w1_after["stock_count"] == 1
    assert "stocks" not in w1_after

    stocks_after_del = await test_watchlist_app_client.get(f"/api/v1/watchlists/{w1_id}/stocks", headers=headers1)
    assert len(stocks_after_del.json()) == 1
    assert stocks_after_del.json()[0]["symbol"] == "TCS"

    # 9. Delete Watchlist 2 ("Banking")
    del_w2_res = await test_watchlist_app_client.delete(f"/api/v1/watchlists/{w2_id}", headers=headers1)
    assert del_w2_res.status_code == 200

    # Verify Watchlist 2 is gone from user's watchlists
    get_after_w2_del = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user1_id}", headers=headers1)
    w2_ids = [w["id"] for w in get_after_w2_del.json()]
    assert w2_id not in w2_ids

    # Verify GET stocks on deleted watchlist returns 404
    deleted_stocks = await test_watchlist_app_client.get(f"/api/v1/watchlists/{w2_id}/stocks", headers=headers1)
    assert deleted_stocks.status_code == 404


@pytest.mark.asyncio
async def test_user_ownership_and_security_isolation(test_watchlist_app_client: AsyncClient):
    """
    Ensure User B cannot access, modify, delete, or add/remove stocks in User A's watchlist.
    """
    # 1. Register User A and create a watchlist with a stock
    res_a = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "alice@sentinews.in", "password": "PasswordAlice123!", "full_name": "Alice"},
    )
    token_a = res_a.json()["access_token"]
    user_a_id = res_a.json()["user"]["id"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    wl_a_res = await test_watchlist_app_client.post(
        "/api/v1/watchlists",
        json={"name": "Alice's Top Stocks"},
        headers=headers_a,
    )
    wl_a_id = wl_a_res.json()["id"]

    await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_a_id}/stocks",
        json={"symbol": "RELIANCE"},
        headers=headers_a,
    )

    # 2. Register User B
    res_b = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "bob@sentinews.in", "password": "PasswordBob123!", "full_name": "Bob"},
    )
    token_b = res_b.json()["access_token"]
    user_b_id = res_b.json()["user"]["id"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 3. User B lists watchlists -> should be empty
    list_b = await test_watchlist_app_client.get("/api/v1/watchlists", headers=headers_b)
    assert list_b.status_code == 200
    assert list_b.json() == []

    # 4. User B attempts to GET User A's watchlists via user_id -> 404 (IDOR protection)
    get_a_by_b = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user_a_id}", headers=headers_b)
    assert get_a_by_b.status_code == 404

    # 4a. User B attempts to GET User A's single watchlist via watchlist_id -> 404 (not found / not owned)
    get_single_by_b = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_a_id}", headers=headers_b)
    assert get_single_by_b.status_code == 404

    # 4b. User B attempts to GET User A's watchlist stocks via watchlist_id -> 404 (not found / not owned)
    get_stocks_by_b = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_a_id}/stocks", headers=headers_b)
    assert get_stocks_by_b.status_code == 404

    # 5. User B attempts to PATCH User A's watchlist -> 404
    patch_a_by_b = await test_watchlist_app_client.patch(
        f"/api/v1/watchlists/{wl_a_id}",
        json={"name": "Hacked by Bob"},
        headers=headers_b,
    )
    assert patch_a_by_b.status_code == 404

    # 6. User B attempts to add stock to User A's watchlist -> 404
    add_stock_by_b = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_a_id}/stocks",
        json={"symbol": "TATAMOTORS"},
        headers=headers_b,
    )
    assert add_stock_by_b.status_code == 404

    # 7. User B attempts to delete stock from User A's watchlist -> 404
    del_stock_by_b = await test_watchlist_app_client.delete(
        f"/api/v1/watchlists/{wl_a_id}/stocks/RELIANCE",
        headers=headers_b,
    )
    assert del_stock_by_b.status_code == 404

    # 8. User B attempts to delete User A's watchlist -> 404
    del_wl_by_b = await test_watchlist_app_client.delete(
        f"/api/v1/watchlists/{wl_a_id}",
        headers=headers_b,
    )
    assert del_wl_by_b.status_code == 404

    # Verify User A's watchlist summary remains completely untouched (no stocks array)
    verify_a = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user_a_id}", headers=headers_a)
    assert verify_a.status_code == 200
    wl_a_summary = next(w for w in verify_a.json() if w["id"] == wl_a_id)
    assert wl_a_summary["name"] == "Alice's Top Stocks"
    assert wl_a_summary["stock_count"] == 1
    assert "stocks" not in wl_a_summary

    # Verify User A can fetch single watchlist with full detail
    verify_a_single = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_a_id}", headers=headers_a)
    assert verify_a_single.status_code == 200
    single_res_data = verify_a_single.json()
    assert single_res_data["name"] == "Alice's Top Stocks"
    assert single_res_data["stock_count"] == 1
    assert len(single_res_data["stocks"]) == 1
    assert single_res_data["stocks"][0]["symbol"] == "RELIANCE"

    # Verify User A can fetch stocks via the dedicated endpoint
    verify_stocks = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_a_id}/stocks", headers=headers_a)
    assert verify_stocks.status_code == 200
    stocks_data = verify_stocks.json()
    assert len(stocks_data) == 1
    assert stocks_data[0]["symbol"] == "RELIANCE"


@pytest.mark.asyncio
async def test_get_single_watchlist_route_selection(test_watchlist_app_client: AsyncClient):
    """
    TEST 1: Get single watchlist.
    Given a valid watchlist ID:
    - Correct route is selected (GET /api/v1/watchlists/{watchlist_id}).
    - Returns WatchlistDetailResponse (dict with stocks), not a list.
    - No user-list handler is accidentally invoked.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "route_test@sentinews.in", "password": "Password123!", "full_name": "Route Tester"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await test_watchlist_app_client.post(
        "/api/v1/watchlists",
        json={"name": "Energy Stocks"},
        headers=headers,
    )
    wl_id = create_res.json()["id"]

    # Add a stock
    await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "ONGC"},
        headers=headers,
    )

    # Fetch single watchlist by ID
    single_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_id}", headers=headers)
    assert single_res.status_code == 200
    data = single_res.json()
    assert isinstance(data, dict)  # NOT a list from user-list handler
    assert data["id"] == wl_id
    assert data["name"] == "Energy Stocks"
    assert data["stock_count"] == 1
    assert len(data["stocks"]) == 1
    assert data["stocks"][0]["symbol"] == "ONGC"


@pytest.mark.asyncio
async def test_get_watchlists_by_user_route_selection(test_watchlist_app_client: AsyncClient):
    """
    TEST 2: Get watchlists by user.
    Given a valid user ID:
    - Correct route is selected (GET /api/v1/watchlists/user/{user_id}).
    - Only that user's watchlists are returned in summary format.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "user_route@sentinews.in", "password": "Password123!", "full_name": "User Route"},
    )
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "WL 1"}, headers=headers)
    await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "WL 2"}, headers=headers)

    res = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 2
    for item in data:
        assert "stocks" not in item
        assert "stock_count" in item


@pytest.mark.asyncio
async def test_get_stocks_by_watchlist_route_selection(test_watchlist_app_client: AsyncClient):
    """
    TEST 3: Get stocks by watchlist.
    Given a valid watchlist ID:
    - Correct route is selected (GET /api/v1/watchlists/{watchlist_id}/stocks).
    - Correct stocks are returned.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "stocks_route@sentinews.in", "password": "Password123!", "full_name": "Stocks Route"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Pharma"}, headers=headers)
    wl_id = create_res.json()["id"]

    await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "SUNPHARMA"},
        headers=headers,
    )
    await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "CIPLA"},
        headers=headers,
    )

    stocks_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_id}/stocks", headers=headers)
    assert stocks_res.status_code == 200
    stocks = stocks_res.json()
    assert len(stocks) == 2
    symbols = [s["symbol"] for s in stocks]
    assert "SUNPHARMA" in symbols
    assert "CIPLA" in symbols


@pytest.mark.asyncio
async def test_add_stock_and_duplicate_conflict_409(test_watchlist_app_client: AsyncClient):
    """
    TEST 4 & 5: Add new stock and duplicate prevention.
    - Adding new stock returns 201 Created and creates relationship exactly once.
    - Adding duplicate stock returns HTTP 409 Conflict with standard error detail.
    - No duplicate relationship created.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dup_test@sentinews.in", "password": "Password123!", "full_name": "Dup Tester"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Auto"}, headers=headers)
    wl_id = create_res.json()["id"]

    # First add: Success
    res1 = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "MARUTI"},
        headers=headers,
    )
    assert res1.status_code == 201
    assert res1.json()["symbol"] == "MARUTI"

    # Second add: Duplicate returns 409 Conflict
    res2 = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "maruti"},
        headers=headers,
    )
    assert res2.status_code == 409
    assert "already present" in res2.json()["detail"].lower()

    # Verify only 1 stock exists in watchlist
    stocks_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_id}/stocks", headers=headers)
    assert len(stocks_res.json()) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_stock_insertion(test_watchlist_app_client: AsyncClient):
    """
    TEST 6: Concurrent duplicate stock insertion.
    Simulate concurrent attempts to add the same stock to the same watchlist.
    Expected:
    - Database unique constraint prevents duplication.
    - Exactly one operation succeeds (HTTP 201).
    - The concurrent duplicate receives HTTP 409 Conflict (NEVER HTTP 500).
    - Exactly one relationship exists afterward.
    - Database/session remains consistent and usable.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "concurrent@sentinews.in", "password": "Password123!", "full_name": "Concurrent Tester"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "FMCG"}, headers=headers)
    wl_id = create_res.json()["id"]

    # Execute concurrent requests attempting to insert ITC simultaneously
    req1 = test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "ITC", "exchange": "NSE"},
        headers=headers,
    )
    req2 = test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "ITC", "exchange": "NSE"},
        headers=headers,
    )

    results = await asyncio.gather(req1, req2)
    status_codes = sorted([r.status_code for r in results])

    # Exactly one must succeed (201) and one must be conflict (409)
    assert status_codes == [201, 409], f"Unexpected status codes: {status_codes}"

    conflict_res = next(r for r in results if r.status_code == 409)
    assert "already present" in conflict_res.json()["detail"].lower()

    # Verify database consistency: exactly one item exists
    stocks_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/{wl_id}/stocks", headers=headers)
    assert stocks_res.status_code == 200
    stocks = stocks_res.json()
    assert len(stocks) == 1
    assert stocks[0]["symbol"] == "ITC"

    # Verify session remains healthy: subsequent requests continue to work
    add_another = await test_watchlist_app_client.post(
        f"/api/v1/watchlists/{wl_id}/stocks",
        json={"symbol": "HINDUNILVR"},
        headers=headers,
    )
    assert add_another.status_code == 201


@pytest.mark.asyncio
async def test_watchlist_summary_optimization(test_watchlist_app_client: AsyncClient):
    """
    TEST 7: Watchlist summary optimization.
    - Correct summary information and stock count returned.
    - 'stocks' collection is NOT present in the summary response.
    - The repository get_user_watchlists_summary computes count at DB level without loading items.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "summary_opt@sentinews.in", "password": "Password123!", "full_name": "Summary Tester"},
    )
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create 3 watchlists: 0 stocks, 1 stock, 2 stocks
    wl1 = (await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Empty"}, headers=headers)).json()["id"]
    wl2 = (await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Single"}, headers=headers)).json()["id"]
    wl3 = (await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Double"}, headers=headers)).json()["id"]

    await test_watchlist_app_client.post(f"/api/v1/watchlists/{wl2}/stocks", json={"symbol": "AAPL"}, headers=headers)
    await test_watchlist_app_client.post(f"/api/v1/watchlists/{wl3}/stocks", json={"symbol": "MSFT"}, headers=headers)
    await test_watchlist_app_client.post(f"/api/v1/watchlists/{wl3}/stocks", json={"symbol": "GOOGL"}, headers=headers)

    # Call summary endpoint
    res = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user_id}", headers=headers)
    assert res.status_code == 200
    summaries = res.json()
    assert len(summaries) == 3

    s_empty = next(s for s in summaries if s["id"] == wl1)
    s_single = next(s for s in summaries if s["id"] == wl2)
    s_double = next(s for s in summaries if s["id"] == wl3)

    assert s_empty["stock_count"] == 0
    assert "stocks" not in s_empty

    assert s_single["stock_count"] == 1
    assert "stocks" not in s_single

    assert s_double["stock_count"] == 2
    assert "stocks" not in s_double



# ---------------------------------------------------------------------------
# Duplicate watchlist name prevention — API integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_watchlist_name_returns_409(test_watchlist_app_client: AsyncClient):
    """
    TEST DW-1: Same user creating a watchlist with an already-used name gets HTTP 409 Conflict.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dw1@sentinews.in", "password": "Password123!", "full_name": "DW1"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # First creation succeeds
    res1 = await test_watchlist_app_client.post(
        "/api/v1/watchlists", json={"name": "Bankings"}, headers=headers
    )
    assert res1.status_code == 201

    # Second creation with same name must conflict
    res2 = await test_watchlist_app_client.post(
        "/api/v1/watchlists", json={"name": "Bankings"}, headers=headers
    )
    assert res2.status_code == 409
    assert "already exists" in res2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_different_users_same_watchlist_name_both_succeed(test_watchlist_app_client: AsyncClient):
    """
    TEST DW-2: Two different users can each create a watchlist with the same name.
    Uniqueness is per-user, not global.
    """
    reg_a = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dw2a@sentinews.in", "password": "Password123!", "full_name": "DW2A"},
    )
    headers_a = {"Authorization": f"Bearer {reg_a.json()['access_token']}"}

    reg_b = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dw2b@sentinews.in", "password": "Password123!", "full_name": "DW2B"},
    )
    headers_b = {"Authorization": f"Bearer {reg_b.json()['access_token']}"}

    res_a = await test_watchlist_app_client.post(
        "/api/v1/watchlists", json={"name": "Tech"}, headers=headers_a
    )
    res_b = await test_watchlist_app_client.post(
        "/api/v1/watchlists", json={"name": "Tech"}, headers=headers_b
    )

    assert res_a.status_code == 201
    assert res_b.status_code == 201
    # Both are distinct watchlists
    assert res_a.json()["id"] != res_b.json()["id"]


@pytest.mark.asyncio
async def test_rename_watchlist_to_existing_name_returns_409(test_watchlist_app_client: AsyncClient):
    """
    TEST DW-3: Renaming a watchlist to a name already owned by the same user returns HTTP 409 Conflict.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dw3@sentinews.in", "password": "Password123!", "full_name": "DW3"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    await test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Alpha"}, headers=headers)
    beta = await test_watchlist_app_client.post(
        "/api/v1/watchlists", json={"name": "Beta"}, headers=headers
    )
    beta_id = beta.json()["id"]

    # Try to rename "Beta" -> "Alpha" (name already taken)
    patch_res = await test_watchlist_app_client.patch(
        f"/api/v1/watchlists/{beta_id}", json={"name": "Alpha"}, headers=headers
    )
    assert patch_res.status_code == 409
    assert "already exists" in patch_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rename_watchlist_to_same_name_succeeds(test_watchlist_app_client: AsyncClient):
    """
    TEST DW-4: Renaming a watchlist to its own current name succeeds with HTTP 200.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dw4@sentinews.in", "password": "Password123!", "full_name": "DW4"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    wl = await test_watchlist_app_client.post(
        "/api/v1/watchlists", json={"name": "Stable"}, headers=headers
    )
    wl_id = wl.json()["id"]

    patch_res = await test_watchlist_app_client.patch(
        f"/api/v1/watchlists/{wl_id}", json={"name": "Stable"}, headers=headers
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Stable"


@pytest.mark.asyncio
async def test_concurrent_duplicate_watchlist_name_creation(test_watchlist_app_client: AsyncClient):
    """
    TEST DW-5: Concurrent duplicate watchlist name creation.
    Two simultaneous POST requests with the same name for the same user:
    - Exactly one gets HTTP 201.
    - The other gets HTTP 409 Conflict (never HTTP 500).
    - Only one watchlist row is created.
    """
    reg = await test_watchlist_app_client.post(
        "/api/v1/auth/register",
        json={"email": "dw5@sentinews.in", "password": "Password123!", "full_name": "DW5"},
    )
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    req1 = test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Concurrent"}, headers=headers)
    req2 = test_watchlist_app_client.post("/api/v1/watchlists", json={"name": "Concurrent"}, headers=headers)

    results = await asyncio.gather(req1, req2)
    status_codes = sorted([r.status_code for r in results])

    assert status_codes == [201, 409], f"Unexpected status codes: {status_codes}"

    conflict_res = next(r for r in results if r.status_code == 409)
    assert "already exists" in conflict_res.json()["detail"].lower()

    # Only one watchlist should exist with this name
    list_res = await test_watchlist_app_client.get(f"/api/v1/watchlists/user/{user_id}", headers=headers)
    assert list_res.status_code == 200
    concurrent_wls = [w for w in list_res.json() if w["name"] == "Concurrent"]
    assert len(concurrent_wls) == 1
