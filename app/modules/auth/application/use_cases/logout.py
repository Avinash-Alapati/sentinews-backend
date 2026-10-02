"""
Logout Use Case.
"""

from typing import Optional
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    SQLAlchemyRefreshTokenRepository,
)


class LogoutUseCase:
    def __init__(self, refresh_repo: SQLAlchemyRefreshTokenRepository):
        self.refresh_repo = refresh_repo

    async def execute(self, refresh_token: Optional[str] = None, user_id: Optional[int] = None) -> bool:
        """
        Revokes the refresh token family or all user refresh tokens on logout.
        """
        if refresh_token:
            await self.refresh_repo.revoke_token_by_value(refresh_token)
        elif user_id:
            await self.refresh_repo.revoke_all_for_user(user_id)
        return True
