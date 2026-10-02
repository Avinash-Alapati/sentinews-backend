import asyncio
import traceback
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.auth.application.use_cases.register import RegisterUseCase
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


async def main():
    try:
        print("Settings DB URL:", settings.DATABASE_URL)
        async with AsyncSessionLocal() as session:
            user_repo = SQLAlchemyUserRepository(session=session)
            uc = RegisterUseCase(user_repo, settings.SECRET_KEY, settings.ALGORITHM)
            user, token = await uc.execute(
                email="investor@sentinews.in",
                password="SecurePassword123!",
                full_name="Arjun Mehta",
            )
            await session.commit()
            print("Successfully registered user:", user)
            print("Access token:", token)
    except Exception as e:
        print("EXCEPTION CAUGHT:")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
