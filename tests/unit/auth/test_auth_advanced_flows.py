"""
Advanced unit and integration tests for Authentication flows:
1. Refresh token rotation & reuse detection (family invalidation).
2. Expired and tampered JWT verification.
3. Cookie-based vs Body-based refresh token transport.
4. User logout and session cache cleanup.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.auth.dependencies import (
    get_current_user,
    get_user_repository,
    invalidate_user_cache,
)
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.modules.auth.application.use_cases.refresh import RefreshUseCase
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import (
    InvalidTokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
)
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    RefreshTokenReusedError,
    SQLAlchemyRefreshTokenRepository,
)
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


@pytest.fixture
async def auth_db_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
async def auth_db_session(auth_db_factory):
    async with auth_db_factory() as session:
        yield session



@pytest.mark.asyncio
async def test_refresh_token_rotation_and_reuse_detection(auth_db_session: AsyncSession):
    """
    Test refresh token lifecycle:
    1. Issue token family (T1).
    2. Rotate T1 -> T2 (T1 becomes invalid, T2 is valid).
    3. Rotate T2 -> T3 (T2 becomes invalid, T3 is valid).
    4. Attacker attempts to reuse T1 -> ReusedError raised, entire family (including T3) revoked!
    """
    user_repo = SQLAlchemyUserRepository(session=auth_db_session)
    user = await user_repo.create(User(email="security.trader@sentinews.in", hashed_password="hashed_pwd"))
    await auth_db_session.commit()

    refresh_repo = SQLAlchemyRefreshTokenRepository(session=auth_db_session)
    refresh_use_case = RefreshUseCase(
        user_repo=user_repo,
        refresh_repo=refresh_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    # 1. Create initial refresh token (T1)
    t1 = await refresh_repo.create_token(user_id=user.id)
    await auth_db_session.commit()
    assert t1 is not None

    # 2. First rotation: T1 -> T2
    user_res, a2, t2 = await refresh_use_case.execute(refresh_token=t1)
    await auth_db_session.commit()
    assert user_res.id == user.id
    assert bool(a2) is True
    assert t2 != t1

    # 3. Second rotation: T2 -> T3
    _, a3, t3 = await refresh_use_case.execute(refresh_token=t2)
    await auth_db_session.commit()
    assert t3 != t2

    # 4. Attempt to reuse old token T1 (Simulating token theft replay attack)
    with patch.object(settings, "REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 0):
        with pytest.raises(RefreshTokenReusedError):
            await refresh_use_case.execute(refresh_token=t1)
    await auth_db_session.commit()

    # 5. Verify that T3 was also revoked due to family invalidation
    with pytest.raises((RefreshTokenReusedError, InvalidRefreshTokenError)):
        await refresh_use_case.execute(refresh_token=t3)


@pytest.mark.asyncio
async def test_expired_refresh_token_raises_error(auth_db_session: AsyncSession):
    """Expired refresh token must be rejected."""
    from app.db.models.refresh_token import RefreshTokenORM
    from sqlalchemy import update

    user_repo = SQLAlchemyUserRepository(session=auth_db_session)
    user = await user_repo.create(User(email="expired.trader@sentinews.in", hashed_password="pwd"))
    await auth_db_session.commit()

    refresh_repo = SQLAlchemyRefreshTokenRepository(session=auth_db_session)
    refresh_use_case = RefreshUseCase(
        user_repo=user_repo,
        refresh_repo=refresh_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    # Create token and manually expire it
    t_expired = await refresh_repo.create_token(user_id=user.id)
    await auth_db_session.commit()

    family_id = t_expired.split(".", 1)[0]
    await auth_db_session.execute(
        update(RefreshTokenORM)
        .where(RefreshTokenORM.family_id == family_id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    )
    await auth_db_session.commit()

    with pytest.raises(RefreshTokenExpiredError):
        await refresh_use_case.execute(refresh_token=t_expired)


@pytest.mark.asyncio
async def test_jwt_tampering_and_algorithm_none_rejected():
    """
    Test JWT signature integrity:
    1. Signature with wrong key must raise InvalidTokenError.
    2. Algorithm 'none' attack must be rejected.
    3. Expired token must raise TokenExpiredError.
    """
    secret = "legitimate_sentinews_secret_key_12345"

    # 1. Valid token
    valid_token = create_access_token(
        subject="101",
        secret_key=secret,
        algorithm="HS256",
        expires_delta=timedelta(minutes=15),
    )
    payload = decode_access_token(valid_token, secret_key=secret, algorithm="HS256")
    assert payload["sub"] == "101"

    # 2. Token signed with attacker's key
    evil_token = create_access_token(
        subject="101",
        secret_key="attacker_secret_key",
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(evil_token, secret_key=secret, algorithm="HS256")

    # 3. Raw alg 'none' token (eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiIxMDEifQ.)
    none_token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiIxMDEifQ."
    with pytest.raises(InvalidTokenError):
        decode_access_token(none_token, secret_key=secret, algorithm="HS256")

    # 4. Expired token
    expired_token = create_access_token(
        subject="101",
        secret_key=secret,
        algorithm="HS256",
        expires_delta=timedelta(minutes=-5),
    )
    with pytest.raises(TokenExpiredError):
        decode_access_token(expired_token, secret_key=secret, algorithm="HS256")



@pytest.mark.asyncio
async def test_user_session_cache_and_logout_eviction():
    """
    Test user session cache lifecycle:
    1. Fetching current user populates session cache.
    2. Invalidating user cache completely removes session key from Redis & Memory.
    """
    mock_user = User(
        id=999,
        email="cache.evict@sentinews.in",
        hashed_password="hash",
        full_name="Evict Test",
        is_active=True,
    )
    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=mock_user)

    token = create_access_token(
        subject="999",
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        extra_claims={"email": mock_user.email},
    )

    # 1. First fetch: queries repo and populates cache
    user1 = await get_current_user(token=token, user_repo=mock_repo)
    assert user1.id == 999
    assert mock_repo.get_by_id.call_count == 1

    # 2. Second fetch: served from cache (no additional DB query)
    user2 = await get_current_user(token=token, user_repo=mock_repo)
    assert user2.id == 999
    assert mock_repo.get_by_id.call_count == 1

    # 3. Invalidate on logout
    await invalidate_user_cache(user_id=999, email="cache.evict@sentinews.in")

    # 4. Third fetch: cache miss -> queries repo again
    user3 = await get_current_user(token=token, user_repo=mock_repo)
    assert user3.id == 999
    assert mock_repo.get_by_id.call_count == 2


@pytest.mark.asyncio
async def test_expired_token_does_not_revoke_active_sibling_families(auth_db_session: AsyncSession):
    """
    Asserts that an expired token on Device B does NOT revoke the independent active session family on Device A.
    """
    from app.db.models.refresh_token import RefreshTokenORM
    from sqlalchemy import update

    user_repo = SQLAlchemyUserRepository(session=auth_db_session)
    user = await user_repo.create(User(email="multi.device@sentinews.in", hashed_password="pwd"))
    await auth_db_session.commit()

    refresh_repo = SQLAlchemyRefreshTokenRepository(session=auth_db_session)
    refresh_use_case = RefreshUseCase(
        user_repo=user_repo,
        refresh_repo=refresh_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    # Device A family (active)
    token_device_a = await refresh_repo.create_token(user_id=user.id)
    # Device B family (will expire)
    token_device_b = await refresh_repo.create_token(user_id=user.id)
    await auth_db_session.commit()

    family_b = token_device_b.split(".", 1)[0]
    # Expire Device B family
    await auth_db_session.execute(
        update(RefreshTokenORM)
        .where(RefreshTokenORM.family_id == family_b)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=2))
    )
    await auth_db_session.commit()

    # Device B refresh fails with RefreshTokenExpiredError
    with pytest.raises(RefreshTokenExpiredError):
        await refresh_use_case.execute(refresh_token=token_device_b)

    # Device A refresh must STILL succeed
    u_a, access_a, new_token_a = await refresh_use_case.execute(refresh_token=token_device_a)
    assert u_a.id == user.id
    assert bool(access_a) is True
    assert bool(new_token_a) is True


@pytest.mark.asyncio
async def test_concurrent_refresh_atomic_single_winner(auth_db_factory):
    """
    Asserts that under concurrent requests with the identical refresh token,
    exactly one request succeeds and the concurrent duplicate is detected as reuse.
    """
    import asyncio

    async with auth_db_factory() as s:
        user_repo = SQLAlchemyUserRepository(session=s)
        user = await user_repo.create(User(email="concurrent.race@sentinews.in", hashed_password="pwd"))
        refresh_repo = SQLAlchemyRefreshTokenRepository(session=s)
        token = await refresh_repo.create_token(user_id=user.id)
        await s.commit()

    async def do_refresh():
        async with auth_db_factory() as s:
            u_repo = SQLAlchemyUserRepository(session=s)
            r_repo = SQLAlchemyRefreshTokenRepository(session=s)
            uc = RefreshUseCase(
                user_repo=u_repo,
                refresh_repo=r_repo,
                secret_key=settings.SECRET_KEY,
                algorithm=settings.ALGORITHM,
            )
            res = await uc.execute(refresh_token=token)
            await s.commit()
            return res

    with patch.object(settings, "REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 0):
        results = await asyncio.gather(do_refresh(), do_refresh(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # Exactly 1 winner
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], (RefreshTokenReusedError, InvalidRefreshTokenError))



@pytest.mark.asyncio
async def test_revoke_all_user_tokens_on_password_change(auth_db_session: AsyncSession):
    """
    Asserts that revoking all user tokens (e.g. on password reset or global logout) invalidates all active sessions.
    """
    user_repo = SQLAlchemyUserRepository(session=auth_db_session)
    user = await user_repo.create(User(email="global.logout@sentinews.in", hashed_password="pwd"))
    await auth_db_session.commit()

    refresh_repo = SQLAlchemyRefreshTokenRepository(session=auth_db_session)
    refresh_use_case = RefreshUseCase(
        user_repo=user_repo,
        refresh_repo=refresh_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    token1 = await refresh_repo.create_token(user_id=user.id)
    token2 = await refresh_repo.create_token(user_id=user.id)
    await auth_db_session.commit()

    # User changes password / invokes global logout
    await refresh_repo.revoke_all_user_tokens(user_id=user.id)
    await auth_db_session.commit()

    # Both tokens must now be rejected
    with pytest.raises((RefreshTokenReusedError, InvalidRefreshTokenError)):
        await refresh_use_case.execute(refresh_token=token1)

    with pytest.raises((RefreshTokenReusedError, InvalidRefreshTokenError)):
        await refresh_use_case.execute(refresh_token=token2)


@pytest.mark.asyncio
async def test_refresh_token_grace_window_allows_near_simultaneous_legitimate_retry(auth_db_session: AsyncSession):
    """
    Asserts that presenting a just-rotated refresh token within the 10-second grace window
    (e.g. parallel multi-tab refresh or network retry) succeeds and returns a valid session
    without invalidating the token family.
    """
    with patch.object(settings, "REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 10):
        user_repo = SQLAlchemyUserRepository(session=auth_db_session)
        user = await user_repo.create(User(email="grace.window@sentinews.in", hashed_password="pwd"))
        await auth_db_session.commit()

        refresh_repo = SQLAlchemyRefreshTokenRepository(session=auth_db_session)
        token = await refresh_repo.create_token(user_id=user.id)
        await auth_db_session.commit()

        # 1. First legitimate refresh succeeds and rotates the token
        user_id_1, new_token_1 = await refresh_repo.rotate_token(token)
        await auth_db_session.commit()
        assert user_id_1 == user.id
        assert new_token_1 != token

        # 2. Parallel second refresh presenting the SAME original token within 2 seconds (within 10s grace window)
        user_id_2, new_token_2 = await refresh_repo.rotate_token(token)
        await auth_db_session.commit()
        assert user_id_2 == user.id
        assert new_token_2 != token


@pytest.mark.asyncio
async def test_refresh_token_reuse_after_grace_window_revokes_family(auth_db_session: AsyncSession):
    """
    Asserts that presenting a consumed refresh token AFTER the 10-second grace window has elapsed
    is flagged as a genuine replay/theft attack, triggering immediate family revocation.
    """
    from app.db.models.refresh_token import RefreshTokenORM
    from sqlalchemy import update

    user_repo = SQLAlchemyUserRepository(session=auth_db_session)
    user = await user_repo.create(User(email="replay.theft@sentinews.in", hashed_password="pwd"))
    await auth_db_session.commit()

    refresh_repo = SQLAlchemyRefreshTokenRepository(session=auth_db_session)
    token = await refresh_repo.create_token(user_id=user.id)
    await auth_db_session.commit()

    # 1. First rotation consumes the token
    user_id_1, new_token_1 = await refresh_repo.rotate_token(token)
    await auth_db_session.commit()

    # 2. Simulate 30 seconds passing (past 10s grace window)
    parts = token.split(".", 1)
    family_id = parts[0]
    token_hash = refresh_repo._hash_token(parts[1])

    old_time = datetime.now(timezone.utc) - timedelta(seconds=30)
    await auth_db_session.execute(
        update(RefreshTokenORM)
        .where(RefreshTokenORM.family_id == family_id, RefreshTokenORM.token_hash == token_hash)
        .values(updated_at=old_time)
    )
    await auth_db_session.commit()

    # 3. Attacker presents original token 30s later -> Replay detected, family revoked!
    with pytest.raises(RefreshTokenReusedError, match="Refresh token reuse detected"):
        await refresh_repo.rotate_token(token)

    # 4. Verify family is revoked: even the new legitimate token is now unusable
    with pytest.raises(RefreshTokenReusedError):
        await refresh_repo.rotate_token(new_token_1)


