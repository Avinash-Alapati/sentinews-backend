"""
SQLAlchemy ORM model for Market Reports.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import Boolean, Date, DateTime, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class MarketReportORM(Base):
    """
    SQLAlchemy ORM model representing scheduled and manually triggered market reports.
    """
    __tablename__ = "market_reports"

    report_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    sections: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    source_providers: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("report_type", "report_date", name="uq_market_reports_type_date"),
    )
