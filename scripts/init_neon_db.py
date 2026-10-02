import asyncio
from sqlalchemy import text
from app.db.base import Base
from app.db.session import engine


async def main():
    print("Connecting to Neon PostgreSQL...")
    async with engine.begin() as conn:
        print("Enabling pgvector extension if not exists...")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        print("Creating all tables (users, portfolios, holdings, transactions, news_articles)...")
        await conn.run_sync(Base.metadata.create_all)
    print("SUCCESS: All tables created in Neon PostgreSQL!")


if __name__ == "__main__":
    asyncio.run(main())
