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


@pytest.fixture
async def second_user(client):
    """A second registered account, for membership and isolation tests."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "bob@example.com", "password": "secret456", "display_name": "Bob"},
    )
    return resp.json()


@pytest.fixture
async def second_auth_headers(client, second_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "secret456"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def project(client, auth_headers):
    """A project created by alice, who is therefore its facilitator."""
    resp = await client.post(
        "/api/projects",
        json={"name": "Team Alpha", "description": "our retro project"},
        headers=auth_headers,
    )
    return resp.json()


@pytest.fixture
async def project_with_member(client, auth_headers, project, second_user):
    """Alice's project with bob added as a plain member."""
    await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    return project


@pytest.fixture
async def cycle(client, auth_headers, project):
    """An open collecting cycle on alice's project."""
    resp = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    return resp.json()
