"""
Mappers translating between User ORM model and Domain User entity.
"""

from app.db.models.user import User as UserORM
from app.modules.auth.domain.entities import User as DomainUser


def user_orm_to_domain(orm: UserORM) -> DomainUser:
    """Converts a User ORM model to a Domain User entity."""
    return DomainUser(
        id=orm.id,
        email=orm.email,
        hashed_password=orm.hashed_password,
        full_name=orm.full_name,
        is_active=orm.is_active,
        is_superuser=orm.is_superuser,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def user_domain_to_orm(domain: DomainUser) -> UserORM:
    """Converts a Domain User entity to a User ORM model."""
    return UserORM(
        id=domain.id,
        email=domain.email.lower().strip(),
        hashed_password=domain.hashed_password,
        full_name=domain.full_name,
        is_active=domain.is_active,
        is_superuser=domain.is_superuser,
    )
