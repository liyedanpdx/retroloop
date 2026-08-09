"""Liveness and readiness, and what readiness is not allowed to say (#18).

The failure test is the one that matters. A driver error carries the connection
string, and `/api/ready` is reachable from anywhere the app is, so the 503 body
is asserted to be two words and nothing else.
"""

import pytest

from app import database
from app.config import settings


@pytest.mark.asyncio
async def test_liveness_says_only_that_the_process_is_up(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_pings_the_configured_database(client, monkeypatch):
    """A real ping against the configured Mongo, and nothing written.

    The suite initialises Beanie itself rather than through the app's lifespan,
    so the module-level client the app would have set is supplied here. The
    connection is the real one; `ping` is a read.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    monkeypatch.setattr(database, "_client", AsyncIOMotorClient(settings.mongo_url))

    response = await client.get("/api/ready")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_is_503_when_the_ping_fails_and_leaks_nothing(client, monkeypatch):
    class DeadClient:
        class admin:  # noqa: N801 — mirroring the driver's attribute name
            @staticmethod
            async def command(_name):
                raise RuntimeError(
                    f"connection refused to {settings.mongo_url} for {settings.mongo_db_name}"
                )

    monkeypatch.setattr(database, "_client", DeadClient)

    response = await client.get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}

    body = response.text
    for leaked in (settings.mongo_url, settings.mongo_db_name, "Traceback", "RuntimeError"):
        assert leaked not in body, leaked


@pytest.mark.asyncio
async def test_readiness_is_503_before_the_database_is_initialised(client, monkeypatch):
    """No client means no connection, and no reason to claim otherwise."""
    monkeypatch.setattr(database, "_client", None)

    response = await client.get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


@pytest.mark.asyncio
async def test_the_ping_is_the_only_thing_readiness_does(client, monkeypatch):
    """Read-only: it must never create, seed or drop anything (#18)."""
    commands = []

    class RecordingClient:
        class admin:  # noqa: N801
            @staticmethod
            async def command(name):
                commands.append(name)
                return {"ok": 1}

    monkeypatch.setattr(database, "_client", RecordingClient)

    assert (await client.get("/api/ready")).status_code == 200
    assert commands == ["ping"]
