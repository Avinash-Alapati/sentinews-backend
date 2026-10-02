import datetime
from typing import Any
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """
    Base Declarative Class for all SQLAlchemy models in SentiNews.
    Provides automatic pluralized table naming and UTC timestamps.
    """
    __name__: str

    # Generate __tablename__ automatically from class name (e.g. User -> users)
    @declared_attr.directive
    def __tablename__(cls) -> str:
        name = cls.__name__.lower()
        if name.endswith("y"):
            return f"{name[:-1]}ies"
        elif not name.endswith("s"):
            return f"{name}s"
        return name

    id: Mapped[int] = mapped_column(primary_key=True, index=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
