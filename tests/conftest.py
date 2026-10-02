import os
import sys
import pytest
from httpx import AsyncClient, ASGITransport

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Ensure application root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
settings.ENVIRONMENT = "test"


from app.main import app
from app.cache.market_cache import market_cache


@pytest.fixture(autouse=True)
async def reset_cache_state():
    """Clear memory caches before and after each test to prevent test cross-talk."""
    market_cache._memory_cache.clear()
    market_cache._memory_locks.clear()
    yield
    market_cache._memory_cache.clear()
    market_cache._memory_locks.clear()


@pytest.fixture
async def async_client():
    """
    Async HTTP client for testing FastAPI endpoints.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


