"""Add composite performance and query indexes

Revision ID: 004_performance_indexes
Revises: 003_market_reports
Create Date: 2026-09-25 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004_performance_indexes'
down_revision: Union[str, None] = '003_market_reports'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Composite index for chronological transactions querying
    op.create_index(
        'ix_transactions_portfolio_id_timestamp',
        'transactions',
        ['portfolio_id', 'timestamp'],
        unique=False,
    )

    # 2. Composite index for fast holding symbol lookup
    op.create_index(
        'ix_holdings_portfolio_id_symbol',
        'holdings',
        ['portfolio_id', 'symbol'],
        unique=False,
    )

    # 3. Composite index for sorted user watchlists
    op.create_index(
        'ix_watchlists_user_id_created_at',
        'watchlists',
        ['user_id', 'created_at'],
        unique=False,
    )

    # 4. Composite index for latest published market report queries
    op.create_index(
        'ix_market_reports_type_status_date',
        'market_reports',
        ['report_type', 'status', 'report_date'],
        unique=False,
    )

    # 5. Composite index for active news articles
    op.create_index(
        'ix_news_articles_expires_published',
        'news_articles',
        ['expires_at', 'published_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_news_articles_expires_published', table_name='news_articles')
    op.drop_index('ix_market_reports_type_status_date', table_name='market_reports')
    op.drop_index('ix_watchlists_user_id_created_at', table_name='watchlists')
    op.drop_index('ix_holdings_portfolio_id_symbol', table_name='holdings')
    op.drop_index('ix_transactions_portfolio_id_timestamp', table_name='transactions')
