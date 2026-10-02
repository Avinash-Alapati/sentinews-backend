"""
FastAPI Auth Dependencies.
"""

from datetime import datetime
import json
import logging
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.market_cache import market_cache
from app.core.config import settings
from app.db.session import get_db
from app.modules.auth.application.use_cases.google_login import GoogleLoginUseCase
from app.modules.auth.application.use_cases.login import LoginUseCase
from app.modules.auth.application.use_cases.logout import LogoutUseCase
from app.modules.auth.application.use_cases.refresh import RefreshUseCase
from app.modules.auth.application.use_cases.register import RegisterUseCase
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import (
    InvalidTokenError,
    TokenExpiredError,
    decode_access_token,
)
from app.modules.auth.infrastructure.oauth.google import GoogleOAuthProvider
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    SQLAlchemyRefreshTokenRepository,
)
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository

logger = logging.getLogger("sentinews.auth.dependencies")

USER_SESSION_CACHE_TTL = 60  # 60 seconds TTL

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/token",
    auto_error=False,
)
http_bearer = HTTPBearer(auto_error=False)


async def invalidate_user_cache(user_id: Optional[int] = None, email: Optional[str] = None) -> None:
    """
    Invalidates both Redis and in-memory user session cache on logout, role change, or profile/password update.
    """
    try:
        if user_id is not None:
            await market_cache.delete(f"auth:user_session:{user_id}")
        if email is not None:
            await market_cache.delete(f"auth:user_session:{email.lower().strip()}")
    except Exception as exc:
        logger.debug("Failed invalidating user session cache: %s", exc)



async def get_token_from_header_or_oauth2(
    oauth_token: Optional[str] = Depends(oauth2_scheme),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> Optional[str]:
    if bearer and bearer.credentials:
        return bearer.credentials
    if oauth_token:
        return oauth_token
    return None


def get_user_repository(db: AsyncSession = Depends(get_db)) -> SQLAlchemyUserRepository:
    return SQLAlchemyUserRepository(session=db)


def get_refresh_token_repository(db: AsyncSession = Depends(get_db)) -> SQLAlchemyRefreshTokenRepository:
    return SQLAlchemyRefreshTokenRepository(session=db)


def get_register_use_case(
    user_repo: SQLAlchemyUserRepository = Depends(get_user_repository),
    refresh_repo: SQLAlchemyRefreshTokenRepository = Depends(get_refresh_token_repository),
) -> RegisterUseCase:
    return RegisterUseCase(
        user_repo=user_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        refresh_repo=refresh_repo,
    )


def get_login_use_case(
    user_repo: SQLAlchemyUserRepository = Depends(get_user_repository),
    refresh_repo: SQLAlchemyRefreshTokenRepository = Depends(get_refresh_token_repository),
) -> LoginUseCase:
    return LoginUseCase(
        user_repo=user_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        refresh_repo=refresh_repo,
    )


def get_refresh_use_case(
    user_repo: SQLAlchemyUserRepository = Depends(get_user_repository),
    refresh_repo: SQLAlchemyRefreshTokenRepository = Depends(get_refresh_token_repository),
) -> RefreshUseCase:
    return RefreshUseCase(
        user_repo=user_repo,
        refresh_repo=refresh_repo,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def get_logout_use_case(
    refresh_repo: SQLAlchemyRefreshTokenRepository = Depends(get_refresh_token_repository),
) -> LogoutUseCase:
    return LogoutUseCase(refresh_repo=refresh_repo)


def get_google_oauth_provider() -> GoogleOAuthProvider:
    return GoogleOAuthProvider(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


def get_google_login_use_case(
    user_repo: SQLAlchemyUserRepository = Depends(get_user_repository),
    oauth_provider: GoogleOAuthProvider = Depends(get_google_oauth_provider),
) -> GoogleLoginUseCase:
    return GoogleLoginUseCase(
        user_repo=user_repo,
        oauth_provider=oauth_provider,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


async def get_current_user(
    token: Optional[str] = Depends(get_token_from_header_or_oauth2),
    user_repo: SQLAlchemyUserRepository = Depends(get_user_repository),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(
            token=token,
            secret_key=settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Fast path: check session cache (Redis + in-memory fallback, 60s TTL)
    cache_key = f"auth:user_session:{user_id_str}"
    try:
        user_dict = await market_cache.get_json(cache_key)
        if user_dict and isinstance(user_dict, dict):
            user = User(
                id=user_dict.get("id"),
                email=user_dict["email"],
                hashed_password=user_dict.get("hashed_password", ""),
                full_name=user_dict.get("full_name"),
                is_active=user_dict.get("is_active", True),
                is_superuser=user_dict.get("is_superuser", False),
                created_at=datetime.fromisoformat(user_dict["created_at"]) if user_dict.get("created_at") else None,
                updated_at=datetime.fromisoformat(user_dict["updated_at"]) if user_dict.get("updated_at") else None,
            )
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User account is inactive",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return user
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("Session cache lookup failed for %s: %s", cache_key, exc)

    # 2. Cache miss: DB query via UserRepository
    if user_id_str.isdigit():
        user = await user_repo.get_by_id(int(user_id_str))
    else:
        user = await user_repo.get_by_email(user_id_str)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Populate session cache (60s TTL) for subsequent requests
    try:
        user_dict = {
            "id": user.id,
            "email": user.email,
            "hashed_password": user.hashed_password,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }
        await market_cache.set_json(cache_key, user_dict, ttl_seconds=USER_SESSION_CACHE_TTL)
        if user.id and str(user.id) != user_id_str:
            await market_cache.set_json(f"auth:user_session:{user.id}", user_dict, ttl_seconds=USER_SESSION_CACHE_TTL)
        if user.email and user.email != user_id_str:
            await market_cache.set_json(f"auth:user_session:{user.email}", user_dict, ttl_seconds=USER_SESSION_CACHE_TTL)
    except Exception as exc:
        logger.debug("Failed populating user session cache: %s", exc)

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user
