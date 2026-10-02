"""
SQLAlchemy repository implementing UserRepository port.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User as UserORM
from app.modules.auth.application.ports import UserRepository
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.mappers import user_domain_to_orm, user_orm_to_domain


class SQLAlchemyUserRepository(UserRepository):
    """
    Asynchronous SQLAlchemy repository for User persistence.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> Optional[User]:
        stmt = select(UserORM).where(UserORM.id == user_id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return user_orm_to_domain(orm)

    async def get_by_email(self, email: str) -> Optional[User]:
        normalized = email.lower().strip()
        stmt = select(UserORM).where(UserORM.email == normalized)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return user_orm_to_domain(orm)

    async def create(self, user: User) -> User:
        orm = user_domain_to_orm(user)
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return user_orm_to_domain(orm)

    async def update(self, user: User) -> User:
        stmt = select(UserORM).where(UserORM.id == user.id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one()
        orm.email = user.email.lower().strip()
        orm.hashed_password = user.hashed_password
        orm.full_name = user.full_name
        orm.is_active = user.is_active
        orm.is_superuser = user.is_superuser
        await self.session.flush()
        await self.session.refresh(orm)

        try:
            from app.api.v1.auth.dependencies import invalidate_user_cache
            await invalidate_user_cache(user_id=orm.id, email=orm.email)
        except Exception:
            pass

        return user_orm_to_domain(orm)
