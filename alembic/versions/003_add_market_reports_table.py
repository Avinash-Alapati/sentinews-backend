"""Add market reports table

Revision ID: 003_market_reports
Revises: 002_news_compliance
Create Date: 2026-09-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003_market_reports'
down_revision: Union[str, None] = '002_news_compliance'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'market_reports',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('report_type', sa.String(length=30), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sections', sa.JSON(), nullable=False),
        sa.Column('disclaimer', sa.Text(), nullable=False),
        sa.Column('source_providers', sa.JSON(), nullable=False),
        sa.Column('is_partial', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('error_details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_type', 'report_date', name='uq_market_reports_type_date')
    )
    op.create_index(op.f('ix_market_reports_id'), 'market_reports', ['id'], unique=False)
    op.create_index(op.f('ix_market_reports_report_type'), 'market_reports', ['report_type'], unique=False)
    op.create_index(op.f('ix_market_reports_report_date'), 'market_reports', ['report_date'], unique=False)
    op.create_index(op.f('ix_market_reports_status'), 'market_reports', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_market_reports_status'), table_name='market_reports')
    op.drop_index(op.f('ix_market_reports_report_date'), table_name='market_reports')
    op.drop_index(op.f('ix_market_reports_report_type'), table_name='market_reports')
    op.drop_index(op.f('ix_market_reports_id'), table_name='market_reports')
    op.drop_table('market_reports')
