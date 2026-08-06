import pytest
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.database import DOCUMENT_MODELS
from app.main import app

TEST_DB_NAME = f"{settings.mongo_db_name}_test"


@pytest.fixture(autouse=True)
async def init_test_db():
    client = AsyncIOMotorClient(settings.mongo_url)
    db = client[TEST_DB_NAME]
    await init_beanie(database=db, document_models=DOCUMENT_MODELS)
    yield
    for name in await db.list_collection_names():
        await db[name].delete_many({})
    client.close()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def registered_user(client):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "secret123", "display_name": "Alice"},
    )
    return resp.json()


@pytest.fixture
async def auth_headers(client, registered_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
