"""
Register Use Case.
"""

import asyncio
from typing import Any, Optional, Tuple, Union
from app.modules.auth.application.ports import UserRepository
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token, get_password_hash


class UserAlreadyExistsError(Exception):
    pass


class RegisterUseCase:
    def __init__(
        self,
        user_repo: UserRepository,
        secret_key: str,
        algorithm: str,
        refresh_repo: Optional[Any] = None,
    ):
        self.user_repo = user_repo
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.refresh_repo = refresh_repo

    async def execute(
        self,
        email: str,
        password: str,
        full_name: Optional[str] = None,
    ) -> Union[Tuple[User, str], Tuple[User, str, Optional[str]]]:
        normalized_email = email.lower().strip()
        existing = await self.user_repo.get_by_email(normalized_email)
        if existing is not None:
            raise UserAlreadyExistsError(f"User with email '{normalized_email}' already exists")

        # Offload CPU-bound bcrypt work to thread pool to avoid blocking the event loop
        hashed_pwd = await asyncio.to_thread(get_password_hash, password)
        new_user = User(
            email=normalized_email,
            hashed_password=hashed_pwd,
            full_name=full_name,
            is_active=True,
        )
        created_user = await self.user_repo.create(new_user)
        access_token = create_access_token(
            subject=str(created_user.id or created_user.email),
            secret_key=self.secret_key,
            algorithm=self.algorithm,
            extra_claims={"email": created_user.email},
        )

        refresh_token = None
        if self.refresh_repo and created_user.id:
            refresh_token = await self.refresh_repo.create_token(user_id=created_user.id)

        return created_user, access_token, refresh_token
