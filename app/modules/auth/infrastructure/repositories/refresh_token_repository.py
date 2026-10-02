"""
SQLAlchemy Repository for Refresh Tokens with Family Tracking and Reuse Detection.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets
from typing import Optional, Tuple
import uuid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.refresh_token import RefreshTokenORM

logger = logging.getLogger("sentinews.auth.refresh_token")


class RefreshTokenError(Exception):
    """Base exception for refresh token failures."""
    pass


class InvalidRefreshTokenError(RefreshTokenError):
    pass


class RefreshTokenExpiredError(RefreshTokenError):
    pass


class RefreshTokenReusedError(RefreshTokenError):
    """Raised when a previously consumed or revoked refresh token is presented."""
    pass


class SQLAlchemyRefreshTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    async def create_token(
        self,
        user_id: int,
        family_id: Optional[str] = None,
    ) -> str:
        """
        Creates a new rotating refresh token in a new or existing family.
        Returns the combined composite token string (family_id.raw_token).
        """
        fid = family_id or uuid.uuid4().hex
        raw_secret = secrets.token_urlsafe(48)
        token_hash = self._hash_token(raw_secret)
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        orm = RefreshTokenORM(
            user_id=user_id,
            token_hash=token_hash,
            family_id=fid,
            is_used=False,
            is_revoked=False,
            expires_at=expires_at,
        )
        self.session.add(orm)
        await self.session.flush()
        return f"{fid}.{raw_secret}"

    async def rotate_token(self, composite_token: str) -> Tuple[int, str]:
        """
        Validates the refresh token, marks it as consumed, checks for reuse,
        and generates a new rotating token in the same family.

        Returns:
            Tuple of (user_id, new_composite_token)
        """
        parts = composite_token.strip().split(".", 1)
        if len(parts) != 2:
            raise InvalidRefreshTokenError("Malformed refresh token structure")

        family_id, raw_secret = parts
        token_hash = self._hash_token(raw_secret)

        # 1. Atomic Test-and-Set: Atomically claim the token only if it is unused and unrevoked
        now = datetime.now(timezone.utc)
        claim_stmt = (
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.family_id == family_id,
                RefreshTokenORM.token_hash == token_hash,
                RefreshTokenORM.is_used.is_(False),
                RefreshTokenORM.is_revoked.is_(False),
            )
            .values(is_used=True, updated_at=now)
        )
        claim_res = await self.session.execute(claim_stmt)
        await self.session.flush()

        if claim_res.rowcount == 0:
            # Check if token exists in the database
            check_stmt = select(RefreshTokenORM).where(
                RefreshTokenORM.family_id == family_id,
                RefreshTokenORM.token_hash == token_hash,
            )
            check_res = await self.session.execute(check_stmt)
            existing = check_res.scalar_one_or_none()
            if existing is None:
                await self.revoke_family(family_id)
                raise InvalidRefreshTokenError("Refresh token not found or invalid")

            if existing.is_revoked:
                raise RefreshTokenReusedError("Refresh token reuse detected. Family revoked for security.")

            # Check legitimate near-simultaneous refresh race condition grace window
            grace_seconds = getattr(settings, "REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 10)
            token_updated_at = (
                existing.updated_at.replace(tzinfo=timezone.utc)
                if existing.updated_at and existing.updated_at.tzinfo is None
                else (existing.updated_at or existing.created_at.replace(tzinfo=timezone.utc) if existing.created_at and existing.created_at.tzinfo is None else (existing.created_at or now))
            )
            elapsed = (now - token_updated_at).total_seconds()

            if existing.is_used and elapsed <= grace_seconds:
                logger.info(
                    "Near-simultaneous refresh race condition detected (elapsed=%.2fs <= grace=%ds) for user_id=%d, family=%s. Issuing valid sibling token.",
                    elapsed,
                    grace_seconds,
                    existing.user_id,
                    family_id,
                )
                new_composite = await self.create_token(user_id=existing.user_id, family_id=family_id)
                return existing.user_id, new_composite

            # Token was used beyond the grace window -> genuine replay/theft detected!
            logger.warning(
                "Security alert: Refresh token reuse detected for user_id=%d, family_id=%s (elapsed=%.2fs > grace=%ds)! Revoking entire token family.",
                existing.user_id,
                family_id,
                elapsed,
                grace_seconds,
            )
            await self.revoke_family(family_id)
            raise RefreshTokenReusedError("Refresh token reuse detected. Family revoked for security.")

        # 2. Token successfully claimed: fetch record to verify expiration
        stmt = select(RefreshTokenORM).where(
            RefreshTokenORM.family_id == family_id,
            RefreshTokenORM.token_hash == token_hash,
        )
        result = await self.session.execute(stmt)
        token_orm = result.scalar_one()
        user_id = token_orm.user_id

        # 3. Expiration Check
        token_expires_at = (
            token_orm.expires_at.replace(tzinfo=timezone.utc)
            if token_orm.expires_at.tzinfo is None
            else token_orm.expires_at
        )
        if token_expires_at < now:
            token_orm.is_revoked = True
            await self.session.flush()
            raise RefreshTokenExpiredError("Refresh token has expired")

        # 4. Issue rotated token in same family
        new_composite = await self.create_token(user_id=user_id, family_id=family_id)
        return user_id, new_composite

    async def revoke_family(self, family_id: str) -> None:
        """Revokes all tokens in a token family (e.g. on reuse or logout)."""
        stmt = (
            update(RefreshTokenORM)
            .where(RefreshTokenORM.family_id == family_id)
            .values(is_revoked=True, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def revoke_token_by_value(self, composite_token: str) -> None:
        """Revokes the family of the given token."""
        parts = composite_token.strip().split(".", 1)
        if len(parts) == 2:
            await self.revoke_family(parts[0])

    async def revoke_all_for_user(self, user_id: int) -> None:
        """Revokes all refresh tokens for a user."""
        stmt = (
            update(RefreshTokenORM)
            .where(RefreshTokenORM.user_id == user_id)
            .values(is_revoked=True, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    revoke_all_user_tokens = revoke_all_for_user

