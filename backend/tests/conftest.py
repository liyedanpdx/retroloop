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
async def outsider_auth_headers(client):
    """A third registered account that belongs to no project at all."""
    await client.post(
        "/api/auth/register",
        json={"email": "carol@example.com", "password": "secret789", "display_name": "Carol"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"email": "carol@example.com", "password": "secret789"},
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


@pytest.fixture
async def shared_cycle(client, auth_headers, project_with_member):
    """An open collecting cycle on a project alice and bob both belong to."""
    resp = await client.post(
        f"/api/projects/{project_with_member['id']}/cycles", headers=auth_headers
    )
    return resp.json()


@pytest.fixture
async def reveal(client, auth_headers):
    """Start the retro on a cycle, which reveals and freezes its cards."""

    async def _reveal(cycle_id, headers=None):
        resp = await client.post(
            f"/api/cycles/{cycle_id}/retro", headers=headers or auth_headers
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _reveal


@pytest.fixture
async def add_card(client, auth_headers):
    """Put a feedback card on a cycle. Only works while the cycle is collecting."""

    async def _add_card(cycle_id, headers=None, **overrides):
        payload = {"category": "start", "text": "pair more often"}
        payload.update(overrides)
        resp = await client.post(
            f"/api/cycles/{cycle_id}/feedback", json=payload, headers=headers or auth_headers
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _add_card


@pytest.fixture
async def advance_phase(client, auth_headers):
    """Move a retro one phase forward. Only the facilitator may do this."""

    async def _advance(retro_id, phase, headers=None):
        resp = await client.patch(
            f"/api/retros/{retro_id}/phase",
            json={"phase": phase},
            headers=headers or auth_headers,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _advance


@pytest.fixture
async def clustering_retro(client, shared_cycle, add_card, reveal, advance_phase):
    """A retro sitting in the cluster phase, on a cycle alice and bob both share.

    The cards are created here because a card cannot be added once the cycle has
    left collecting, and the cluster phase is well past that point.

    Returns `{"cycle": ..., "retro": ..., "cards": [...]}`.
    """
    cards = [
        await add_card(shared_cycle["id"], text="pair more often"),
        await add_card(shared_cycle["id"], text="stand-ups run long"),
        await add_card(shared_cycle["id"], text="keep the demo"),
    ]
    retro = await reveal(shared_cycle["id"])
    retro = await advance_phase(retro["id"], "cluster")
    return {"cycle": shared_cycle, "retro": retro, "cards": cards}
