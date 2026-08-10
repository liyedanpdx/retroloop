import asyncio
import os

import pytest
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.database import DOCUMENT_MODELS
from app.main import app

# Under plain `pytest`, one process and one `_test` database, same as always.
# Under `pytest-xdist` (`-n auto`), each worker is its own process with its own
# session-scoped Mongo connection and event loop (safe — see `_test_db` below),
# but every worker still runs the shared, mutating `_test` database's autouse
# wipe fixture. Without a name per worker, two workers running tests at the
# same moment would delete each other's in-flight data. `PYTEST_XDIST_WORKER`
# (e.g. "gw0") is set by xdist and unset otherwise.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB_NAME = f"{settings.mongo_db_name}_test" + (f"_{_WORKER}" if _WORKER else "")


class UnstubbedProxyCall(BaseException):
    """A test reached the AI proxy without saying what it should answer.

    Deliberately not an `Exception`: `run_extraction` catches `Exception` so a
    dead proxy becomes a stored `failed` status, and this must not be quietly
    turned into one. A forgotten stub has to fail the test, loudly, rather than
    dial a real server from the suite.
    """


class StubbedProxy:
    """The proxy, as every test sees it. Nothing here touches the network.

    A test says what the proxy does — `returns(...)` for an answer,
    `raises(...)` for a failure, `answers(fn)` for anything conditional — and
    can read `calls` afterwards to check what was asked.
    """

    def __init__(self):
        self.calls = []
        self._answer = None

    def answers(self, handler):
        self._answer = handler

    def returns(self, payload: dict):
        self.answers(lambda system, user: payload)

    def raises(self, error: BaseException):
        def _raise(system, user):
            raise error

        self.answers(_raise)

    async def chat_json(self, system: str, user: str) -> dict:
        self.calls.append({"system": system, "user": user})
        if self._answer is None:
            raise UnstubbedProxyCall(
                "the AI proxy was called without a stub — configure ai_proxy in this test"
            )
        return self._answer(system, user)


@pytest.fixture(autouse=True)
def ai_proxy(monkeypatch):
    """Autouse, so no test can make a network call by forgetting to stub (#10).

    Every binding is replaced: the two importers, `app/services/transcript.py`
    (#10) and `app/services/cluster_suggestions.py` (#19), which are what the
    running code calls, and the one on `app/services/ai.py` itself.
    """
    proxy = StubbedProxy()
    monkeypatch.setattr("app.services.ai.chat_json", proxy.chat_json)
    monkeypatch.setattr("app.services.transcript.chat_json", proxy.chat_json)
    monkeypatch.setattr("app.services.cluster_suggestions.chat_json", proxy.chat_json)
    return proxy


@pytest.fixture(scope="session")
async def _test_db():
    """One connection and one Beanie registration for the whole run.

    Both used to happen per test, which meant every one of ~200 tests paid a
    fresh TCP/TLS handshake to the real, remote Mongo `_docs/decisions.md`
    requires — most of a run's wall time was that handshake, not the test.
    Motor's client is bound to the event loop it is created on, so sharing one
    here is only safe because `pytest.ini` pins every async test and fixture to
    the same session-scoped loop; without that this client would break the
    first time a test tried to use it from a different loop. Beanie's model
    registration is a one-time effect on the `Document` classes themselves and
    was always redundant to repeat.
    """
    client = AsyncIOMotorClient(settings.mongo_url)
    db = client[TEST_DB_NAME]
    await init_beanie(database=db, document_models=DOCUMENT_MODELS)
    yield db
    client.close()


@pytest.fixture(autouse=True)
async def init_test_db(_test_db):
    """Per-test isolation, without paying for a new connection to get it.

    Every test still starts from an empty database — the same guarantee as
    before — but wiping collections on a connection that is already open is a
    local operation, not a network round trip.

    The wipes themselves run concurrently rather than one at a time: each
    collection's `delete_many` is a separate round trip to the same remote
    Mongo, and they do not depend on each other, so doing them one after
    another was paying that latency once per collection for no reason.
    """
    yield
    names = await _test_db.list_collection_names()
    await asyncio.gather(*(_test_db[name].delete_many({}) for name in names))


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


@pytest.fixture
async def add_cluster(client, auth_headers):
    """Put a cluster on a retro. Only works while the retro is in the cluster phase."""

    async def _add_cluster(retro_id, name="Flow", headers=None):
        resp = await client.post(
            f"/api/retros/{retro_id}/clusters",
            json={"name": name},
            headers=headers or auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _add_cluster


@pytest.fixture
async def voting_retro(clustering_retro, add_cluster, advance_phase):
    """A retro in the vote phase with three clusters already on it.

    The clusters are created here because they can only be created during the
    cluster phase, which the vote phase is already past. Alice and bob are both
    members of the underlying project — two eligible voters — and
    `outsider_auth_headers` belongs to neither.

    Returns `{"cycle": ..., "retro": ..., "clusters": [...]}`.
    """
    retro = clustering_retro["retro"]
    clusters = [
        await add_cluster(retro["id"], name=name) for name in ("Flow", "Tooling", "Meetings")
    ]
    retro = await advance_phase(retro["id"], "vote")
    return {"cycle": clustering_retro["cycle"], "retro": retro, "clusters": clusters}


@pytest.fixture
async def discussion_retro(client, auth_headers, second_auth_headers, voting_retro, advance_phase):
    """A retro in the discuss phase, with ballots cast and topics generated.

    Both members vote before the advance so the tally is not a wall of zeroes —
    Flow gets three votes, Tooling one, Meetings none — and the topics come out
    ranked rather than accidentally in insertion order.

    Alice is the facilitator, bob a plain member, and `outsider_auth_headers`
    belongs to neither.

    Returns `{"cycle": ..., "retro": ..., "clusters": [...], "topics": [...]}`,
    the topics in the order the retro stores them.
    """
    retro = voting_retro["retro"]
    flow, tooling, _ = [c["id"] for c in voting_retro["clusters"]]

    for headers, ballot in ((auth_headers, [flow, flow, tooling]), (second_auth_headers, [flow])):
        resp = await client.post(
            f"/api/retros/{retro['id']}/votes", json={"cluster_ids": ballot}, headers=headers
        )
        assert resp.status_code == 201, resp.text

    retro = await advance_phase(retro["id"], "discuss")
    return {
        "cycle": voting_retro["cycle"],
        "retro": retro,
        "clusters": voting_retro["clusters"],
        "topics": retro["topics"],
    }


@pytest.fixture
async def transcript_retro(client, auth_headers, discussion_retro, ai_proxy):
    """The discuss-phase retro with one extraction already finished (#10).

    The stubbed answer is deliberately mixed: one decision, one action whose
    owner is bob — a current member, so it matches — and one action naming
    somebody who is not on the project, so it does not. That is enough setup for
    every confirm test to work from without pasting again.

    Returns `{"cycle": ..., "retro": ..., "topics": [...], "suggestions": ...,
    "decision": ..., "action": ..., "unmatched_action": ...}`, the suggestions
    being the document as `GET /suggestions` returns it.
    """
    retro = discussion_retro["retro"]
    ai_proxy.returns(
        {
            "decisions": [{"text": "Ship the migration behind a flag"}],
            "actions": [
                {"description": "Write the runbook", "owner": "Bob", "due_date": None},
                {"description": "Book the room", "owner": "Dana Wu", "due_date": None},
            ],
        }
    )

    resp = await client.post(
        f"/api/retros/{retro['id']}/transcript",
        json={"text": "alice: let us ship behind a flag. bob: I will write the runbook."},
        headers=auth_headers,
    )
    assert resp.status_code == 202, resp.text

    read = await client.get(f"/api/retros/{retro['id']}/suggestions", headers=auth_headers)
    assert read.status_code == 200, read.text
    suggestions = read.json()
    assert suggestions["status"] == "ready", suggestions

    return {
        "cycle": discussion_retro["cycle"],
        "retro": retro,
        "topics": discussion_retro["topics"],
        "suggestions": suggestions,
        "decision": suggestions["decisions"][0],
        "action": suggestions["actions"][0],
        "unmatched_action": suggestions["actions"][1],
    }
