"""
Login Use Case.
"""

import asyncio
from typing import Any, Optional, Tuple, Union
from app.modules.auth.application.ports import UserRepository
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token, verify_password

# Pre-computed bcrypt dummy hash for uniform constant-time response on non-existent users
DUMMY_BCRYPT_HASH = "$2b$12$e8Y6BqjTqT/m8D5wHjYv9u7J7uW4uP9u7J7uW4uP9u7J7uW4uP9u7"


class InvalidCredentialsError(Exception):
    pass


class InactiveUserError(Exception):
    pass


class LoginUseCase:
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
        self, email: str, password: str
    ) -> Union[Tuple[User, str], Tuple[User, str, Optional[str]]]:
        normalized_email = email.lower().strip()
        user = await self.user_repo.get_by_email(normalized_email)
        if user is None:
            # Timing attack mitigation: run dummy verification so time is identical to wrong password
            await asyncio.to_thread(verify_password, password, DUMMY_BCRYPT_HASH)
            raise InvalidCredentialsError("Incorrect email or password")

        # Offload CPU-bound bcrypt verification to thread pool to avoid blocking the event loop
        is_valid = await asyncio.to_thread(verify_password, password, user.hashed_password)
        if not is_valid:
            raise InvalidCredentialsError("Incorrect email or password")

        if not user.is_active:
            raise InactiveUserError("User account is inactive")

        access_token = create_access_token(
            subject=str(user.id or user.email),
            secret_key=self.secret_key,
            algorithm=self.algorithm,
            extra_claims={"email": user.email},
        )

        refresh_token = None
        if self.refresh_repo and user.id:
            refresh_token = await self.refresh_repo.create_token(user_id=user.id)

        return user, access_token, refresh_token
