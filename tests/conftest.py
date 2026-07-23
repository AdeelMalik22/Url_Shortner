import os

import anyio.to_thread
import pytest
from httpx2 import ASGITransport, AsyncClient


os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["REDIS_CACHE_TTL"] = "0"
os.environ["REDIS_REQUIRED"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["SHORT_CODE_LENGTH"] = "10"
os.environ["SHORT_CODE_MAX_ATTEMPTS"] = "10"
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_REQUIRED"] = "false"
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-long-enough"
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)


from app.utils.db_connection import Base, engine  # noqa: E402
from app.config import get_settings  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def run_sync_handlers_inline(monkeypatch):
    """Keep in-process ASGI tests independent of worker-thread scheduling."""

    async def run_sync_inline(function, *args, **_):
        return function(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync_inline)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
