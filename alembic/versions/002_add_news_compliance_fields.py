"""Add news compliance fields

Revision ID: 002_news_compliance
Revises: 001_initial_schema
Create Date: 2026-09-17 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_news_compliance'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add article_tone, market_context, content_hash to news_articles table
    op.add_column(
        'news_articles',
        sa.Column('article_tone', sa.String(length=20), nullable=False, server_default='neutral')
    )
    op.add_column(
        'news_articles',
        sa.Column('market_context', sa.String(length=20), nullable=False, server_default='market_hours')
    )
    op.add_column(
        'news_articles',
        sa.Column('content_hash', sa.String(length=64), nullable=True)
    )

    op.create_index(op.f('ix_news_articles_article_tone'), 'news_articles', ['article_tone'], unique=False)
    op.create_index(op.f('ix_news_articles_market_context'), 'news_articles', ['market_context'], unique=False)
    op.create_index(op.f('ix_news_articles_content_hash'), 'news_articles', ['content_hash'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_news_articles_content_hash'), table_name='news_articles')
    op.drop_index(op.f('ix_news_articles_market_context'), table_name='news_articles')
    op.drop_index(op.f('ix_news_articles_article_tone'), table_name='news_articles')
    op.drop_column('news_articles', 'content_hash')
    op.drop_column('news_articles', 'market_context')
    op.drop_column('news_articles', 'article_tone')
