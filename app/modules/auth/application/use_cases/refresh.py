"""
Refresh Token Use Case.
"""

from typing import Tuple
from app.modules.auth.application.ports import UserRepository
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    SQLAlchemyRefreshTokenRepository,
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    RefreshTokenReusedError,
)


class RefreshUseCase:
    def __init__(
        self,
        user_repo: UserRepository,
        refresh_repo: SQLAlchemyRefreshTokenRepository,
        secret_key: str,
        algorithm: str,
    ):
        self.user_repo = user_repo
        self.refresh_repo = refresh_repo
        self.secret_key = secret_key
        self.algorithm = algorithm

    async def execute(self, refresh_token: str) -> Tuple[User, str, str]:
        """
        Rotates the refresh token and generates a fresh access token.

        Returns:
            Tuple of (User, new_access_token, new_refresh_token)
        """
        user_id, new_refresh_token = await self.refresh_repo.rotate_token(refresh_token)
        user = await self.user_repo.get_by_id(user_id)
        if user is None or not user.is_active:
            await self.refresh_repo.revoke_token_by_value(new_refresh_token)
            raise InvalidRefreshTokenError("User account not found or deactivated")

        access_token = create_access_token(
            subject=str(user.id),
            secret_key=self.secret_key,
            algorithm=self.algorithm,
            extra_claims={"email": user.email},
        )
        return user, access_token, new_refresh_token
