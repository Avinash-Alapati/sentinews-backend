import asyncio
import asyncpg
from app.core.config import settings

async def main():
    try:
        # Connect using URL
        conn = await asyncpg.connect(settings.SYNC_DATABASE_URL)
        print("Connected to PostgreSQL successfully!")
        await conn.close()
    except Exception as e:
        print(f"Could not connect to PostgreSQL: {e}")

if __name__ == "__main__":
    asyncio.run(main())
