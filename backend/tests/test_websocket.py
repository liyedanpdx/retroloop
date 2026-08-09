"""Real WebSocket connections against the app, driven over ASGI (#12).

There is no `websockets` client in this project's dependencies and adding one
needs a decision in `_docs/decisions.md`, so `Socket` below speaks the ASGI
WebSocket protocol to `app` directly — the same messages a server would hand it,
in the same event loop as the rest of the suite. The route, the query-parameter
auth, the rooms and the close codes are the real ones; only the socket on the
far end is local.

`silence()` is the other half of most of these tests. Half of this issue is
about what must *not* arrive — a rejected request, a no-op `PATCH`, another
project's event — and an assertion that nothing came back needs a bounded wait.
"""

import asyncio
import json

import pytest
from beanie import PydanticObjectId

from app.main import app
from app.models.user import User
from app.services.auth import create_access_token, create_refresh_token
from app.services.realtime import manager

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"

# Long enough that a slow round trip is not read as silence, short enough that a
# suite full of negative assertions does not crawl.
QUIET = 0.25


class Socket:
    """One client connection, opened by `async with Socket(retro_id, token)`."""

    def __init__(self, retro_id: str, token: str | None = None):
        query = "" if token is None else f"token={token}"
        self._scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": f"/ws/retro/{retro_id}",
            "raw_path": f"/ws/retro/{retro_id}".encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
        }
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._outbox: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def __aenter__(self) -> "Socket":
        await self._inbox.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(app(self._scope, self._inbox.get, self._outbox.put))
        first = await asyncio.wait_for(self._outbox.get(), timeout=5)
        if first["type"] == "websocket.accept":
            self.accepted = True
        else:
            self.close_code = first.get("code")
            await self._task
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        """Disconnect the way a browser does: tell the server, then let it tidy."""
        if self._task is None or self._task.done():
            return
        await self._inbox.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, timeout=5)

    async def event(self, timeout: float = 5) -> dict:
        message = await asyncio.wait_for(self._outbox.get(), timeout=timeout)
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])

    async def silence(self) -> None:
        """Assert nothing arrives. The wait is the assertion."""
        with pytest.raises(asyncio.TimeoutError):
            message = await asyncio.wait_for(self._outbox.get(), timeout=QUIET)
            pytest.fail(f"expected no event, got {message}")


async def token_for(client, email: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
async def alice_token(client, registered_user):
    return await token_for(client, "alice@example.com", "secret123")


@pytest.fixture
async def bob_token(client, second_user):
    return await token_for(client, "bob@example.com", "secret456")


@pytest.fixture
async def carol_token(client, outsider_auth_headers):
    """An account on no project at all — authenticated, and still not welcome."""
    return await token_for(client, "carol@example.com", "secret789")


# --- the handshake -----------------------------------------------------------


@pytest.mark.asyncio
async def test_member_connects_and_joins_that_retros_room(
    clustering_retro, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as socket:
        assert socket.accepted
        assert manager.connection_count(retro_id) == 1
    assert manager.connection_count(retro_id) == 0


@pytest.mark.asyncio
async def test_a_plain_member_may_connect_without_being_facilitator(
    clustering_retro, bob_token
):
    """Everyone watches the board; only the REST layer decides who may move it."""
    async with Socket(clustering_retro["retro"]["id"], bob_token) as socket:
        assert socket.accepted


@pytest.mark.asyncio
async def test_every_unauthorized_case_is_closed_with_1008_before_joining(
    client, clustering_retro, alice_token, carol_token
):
    retro_id = clustering_retro["retro"]["id"]

    # A token whose user has been deleted decodes fine and still may not connect.
    ghost = await User(
        email="ghost@example.com", hashed_password="x", display_name="Ghost"
    ).insert()
    ghost_token = create_access_token(str(ghost.id))
    await ghost.delete()

    cases = {
        "no token": (retro_id, None),
        "empty token": (retro_id, ""),
        "malformed token": (retro_id, "not-a-jwt"),
        "signed by nobody": (retro_id, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.wrong"),
        "deleted user": (retro_id, ghost_token),
        "unknown retro": (UNKNOWN_ID, alice_token),
        "malformed retro id": (MALFORMED_ID, alice_token),
        "non-member": (retro_id, carol_token),
    }
    for name, (target, token) in cases.items():
        async with Socket(target, token) as socket:
            assert not socket.accepted, f"{name} was let in"
            assert socket.close_code == 1008, f"{name} closed with {socket.close_code}"
        assert manager.connection_count(target) == 0, f"{name} joined a room"

    # And a refused client is told nothing about the retro it asked for.
    assert manager.rooms() == []


@pytest.mark.asyncio
async def test_an_expired_or_wrong_kind_of_token_is_refused(clustering_retro, registered_user):
    """Expiry and token type are the decoder's job, not re-implemented here.

    The refresh token matters as much as the expired one: it is a valid,
    unexpired JWT this server signed, and the only thing standing between it and
    a socket is `decode_access_token` refusing a `type` that is not `access`.
    """
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.config import settings

    expired = jwt.encode(
        {
            "sub": registered_user["id"],
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
            "type": "access",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    refresh = create_refresh_token(registered_user["id"])

    for token in (expired, refresh):
        async with Socket(clustering_retro["retro"]["id"], token) as socket:
            assert not socket.accepted
            assert socket.close_code == 1008


# --- rooms -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_members_of_a_retro_receive_its_events(
    client, auth_headers, clustering_retro, alice_token, bob_token
):
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as alice, Socket(retro_id, bob_token) as bob:
        assert manager.connection_count(retro_id) == 2
        resp = await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
        )
        assert resp.status_code == 201, resp.text

        # The caller is connected too, and is not excluded from their own event.
        for socket in (alice, bob):
            assert (await socket.event())["event"] == "cluster_created"


@pytest.mark.asyncio
async def test_another_retro_never_sees_this_ones_events(
    client, auth_headers, clustering_retro, add_card, reveal, advance_phase, alice_token
):
    """Two retros, one connected member each, one mutation. Only one room hears."""
    second = await client.post(
        "/api/projects", json={"name": "Team Beta", "description": "other"}, headers=auth_headers
    )
    other_cycle = await client.post(
        f"/api/projects/{second.json()['id']}/cycles", headers=auth_headers
    )
    other_retro = await reveal(other_cycle.json()["id"])
    other_retro = await advance_phase(other_retro["id"], "cluster")

    retro_id = clustering_retro["retro"]["id"]
    async with (
        Socket(retro_id, alice_token) as here,
        Socket(other_retro["id"], alice_token) as elsewhere,
    ):
        resp = await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
        )
        assert resp.status_code == 201, resp.text
        assert (await here.event())["event"] == "cluster_created"
        await elsewhere.silence()


# --- the events --------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_change_broadcasts_the_new_phase(
    client, auth_headers, clustering_retro, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as socket:
        resp = await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "vote"}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        message = await socket.event()
        assert set(message) == {"event", "data"}
        assert message == {"event": "phase_changed", "data": {"phase": "vote"}}


@pytest.mark.asyncio
async def test_closing_the_vote_sends_the_results_first_then_the_phase(
    client, auth_headers, voting_retro, alice_token
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    resp = await client.post(
        f"/api/retros/{retro_id}/votes", json={"cluster_ids": [flow, flow]}, headers=auth_headers
    )
    assert resp.status_code == 201, resp.text

    async with Socket(retro_id, alice_token) as socket:
        advanced = await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "discuss"}, headers=auth_headers
        )
        assert advanced.status_code == 200, advanced.text

        closed = await socket.event()
        changed = await socket.event()

    assert closed["event"] == "voting_closed"
    assert changed == {"event": "phase_changed", "data": {"phase": "discuss"}}

    results = await client.get(f"/api/retros/{retro_id}/votes/results", headers=auth_headers)
    assert results.status_code == 200, results.text
    assert closed["data"] == results.json()
    assert set(closed["data"]) == {"members_voted", "members_total", "total_votes", "results"}
    assert closed["data"]["results"][0]["rank"] == 1
    # A ballot is who voted, never what they chose. Neither shape carries one.
    assert "cluster_ids" not in json.dumps(closed)
    assert "user_id" not in json.dumps(closed)


@pytest.mark.asyncio
async def test_no_other_transition_closes_voting(
    client, auth_headers, clustering_retro, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as socket:
        await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "vote"}, headers=auth_headers
        )
        assert (await socket.event())["event"] == "phase_changed"
        await socket.silence()


@pytest.mark.asyncio
async def test_cluster_created_renamed_and_deleted(
    client, auth_headers, clustering_retro, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    card = clustering_retro["cards"][0]

    async with Socket(retro_id, alice_token) as socket:
        created = await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
        )
        cluster = created.json()
        message = await socket.event()
        assert message["event"] == "cluster_created"
        assert message["data"] == cluster
        assert set(message["data"]) == {"id", "name", "created_at"}

        moved = await client.patch(
            f"/api/feedback/{card['id']}/cluster",
            json={"cluster_id": cluster["id"]},
            headers=auth_headers,
        )
        assert moved.status_code == 200, moved.text
        assert (await socket.event())["event"] == "card_moved"

        renamed = await client.patch(
            f"/api/retros/{retro_id}/clusters/{cluster['id']}",
            json={"name": "Flow and focus"},
            headers=auth_headers,
        )
        assert renamed.status_code == 200, renamed.text
        message = await socket.event()
        assert message["event"] == "cluster_renamed"
        assert message["data"] == renamed.json()
        assert message["data"]["name"] == "Flow and focus"

        deleted = await client.delete(
            f"/api/retros/{retro_id}/clusters/{cluster['id']}", headers=auth_headers
        )
        assert deleted.status_code == 200, deleted.text
        message = await socket.event()
        assert message["event"] == "cluster_deleted"
        assert message["data"] == {"id": cluster["id"], "card_ids": [card["id"]]}


@pytest.mark.asyncio
async def test_card_moved_in_and_out_of_a_cluster(
    client, auth_headers, clustering_retro, add_cluster, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    card = clustering_retro["cards"][0]
    cluster = await add_cluster(retro_id)

    async with Socket(retro_id, alice_token) as socket:
        await client.patch(
            f"/api/feedback/{card['id']}/cluster",
            json={"cluster_id": cluster["id"]},
            headers=auth_headers,
        )
        assert await socket.event() == {
            "event": "card_moved",
            "data": {"card_id": card["id"], "cluster_id": cluster["id"]},
        }

        # Out of a cluster is a move too, and null is the payload for it.
        out = await client.patch(
            f"/api/feedback/{card['id']}/cluster", json={"cluster_id": None}, headers=auth_headers
        )
        assert out.status_code == 200, out.text
        assert await socket.event() == {
            "event": "card_moved",
            "data": {"card_id": card["id"], "cluster_id": None},
        }


@pytest.mark.asyncio
async def test_moving_a_card_where_it_already_is_emits_nothing(
    client, auth_headers, clustering_retro, add_cluster, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    card = clustering_retro["cards"][0]
    cluster = await add_cluster(retro_id)
    await client.patch(
        f"/api/feedback/{card['id']}/cluster",
        json={"cluster_id": cluster["id"]},
        headers=auth_headers,
    )

    async with Socket(retro_id, alice_token) as socket:
        repeat = await client.patch(
            f"/api/feedback/{card['id']}/cluster",
            json={"cluster_id": cluster["id"]},
            headers=auth_headers,
        )
        assert repeat.status_code == 200, "the request is still legal"
        await socket.silence()

        # And a card that is already loose being set loose again is also quiet.
        await client.patch(
            f"/api/feedback/{card['id']}/cluster", json={"cluster_id": None}, headers=auth_headers
        )
        assert (await socket.event())["event"] == "card_moved"
        again = await client.patch(
            f"/api/feedback/{card['id']}/cluster", json={"cluster_id": None}, headers=auth_headers
        )
        assert again.status_code == 200
        await socket.silence()


@pytest.mark.asyncio
async def test_topic_updated_only_when_a_value_actually_changes(
    client, auth_headers, discussion_retro, alice_token
):
    retro_id = discussion_retro["retro"]["id"]
    topic = discussion_retro["topics"][0]
    url = f"/api/retros/{retro_id}/topics/{topic['id']}"

    async with Socket(retro_id, alice_token) as socket:
        updated = await client.patch(
            url, json={"status": "discussed", "notes": "shipped it"}, headers=auth_headers
        )
        assert updated.status_code == 200, updated.text
        message = await socket.event()
        assert message["event"] == "topic_updated"
        assert message["data"] == updated.json()
        assert set(message["data"]) == {
            "id", "cluster_id", "name", "vote_count", "rank", "status", "notes"
        }

        empty = await client.patch(url, json={}, headers=auth_headers)
        assert empty.status_code == 200, "an empty body is a legal no-op"
        await socket.silence()

        same = await client.patch(
            url, json={"status": "discussed", "notes": "shipped it"}, headers=auth_headers
        )
        assert same.status_code == 200
        await socket.silence()


# --- what must not be broadcast ----------------------------------------------


@pytest.mark.asyncio
async def test_a_rejected_request_emits_nothing(
    client, auth_headers, second_auth_headers, outsider_auth_headers, clustering_retro, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as socket:
        # 403 — an outsider cannot cluster.
        refused = await client.post(
            f"/api/retros/{retro_id}/clusters",
            json={"name": "Sneaky"},
            headers=outsider_auth_headers,
        )
        assert refused.status_code == 403
        await socket.silence()

        # 422 — a blank name never reaches the handler.
        blank = await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "   "}, headers=auth_headers
        )
        assert blank.status_code == 422
        await socket.silence()

        # 404 — renaming a cluster that is not there.
        missing = await client.patch(
            f"/api/retros/{retro_id}/clusters/nope", json={"name": "Flow"}, headers=auth_headers
        )
        assert missing.status_code == 404
        await socket.silence()

        # 400 — a phase that is not the next one.
        skipped = await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "discuss"}, headers=auth_headers
        )
        assert skipped.status_code == 400
        await socket.silence()

        # 403 — a member who is not the facilitator cannot advance the phase.
        not_facilitator = await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "vote"}, headers=second_auth_headers
        )
        assert not_facilitator.status_code == 403
        await socket.silence()


# --- lifecycle ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_disconnected_client_leaves_and_the_empty_room_goes_with_it(
    clustering_retro, alice_token, bob_token
):
    retro_id = clustering_retro["retro"]["id"]
    alice = await Socket(retro_id, alice_token).__aenter__()
    bob = await Socket(retro_id, bob_token).__aenter__()
    assert manager.connection_count(retro_id) == 2

    await alice.close()
    assert manager.connection_count(retro_id) == 1
    assert retro_id in manager.rooms(), "bob is still in it"

    await bob.close()
    assert manager.connection_count(retro_id) == 0
    assert retro_id not in manager.rooms(), "the empty room is dropped, not kept"

    # Closing twice is safe, and so is removing a socket that is already gone.
    await alice.close()
    manager.disconnect(retro_id, object())
    manager.disconnect("no such room", object())


@pytest.mark.asyncio
async def test_reconnecting_resumes_events_and_replays_none(
    client, auth_headers, clustering_retro, alice_token
):
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as socket:
        await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "First"}, headers=auth_headers
        )
        assert (await socket.event())["data"]["name"] == "First"

    # Missed while away. Nothing stores it (`_docs/decisions.md`, no replay).
    await client.post(
        f"/api/retros/{retro_id}/clusters", json={"name": "Missed"}, headers=auth_headers
    )

    async with Socket(retro_id, alice_token) as socket:
        assert socket.accepted
        await socket.silence()

        await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Later"}, headers=auth_headers
        )
        assert (await socket.event())["data"]["name"] == "Later"

        # The state it missed is on the REST read, which is where #16 goes for it.
        board = await client.get(f"/api/retros/{retro_id}", headers=auth_headers)
        assert [c["name"] for c in board.json()["clusters"]] == ["First", "Missed", "Later"]


@pytest.mark.asyncio
async def test_a_dead_socket_is_dropped_without_failing_the_rest_call(
    client, auth_headers, clustering_retro, alice_token
):
    """A browser that went away must not turn a committed write into an error."""

    class DeadSocket:
        async def send_json(self, message):
            raise RuntimeError("connection reset")

    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as live:
        dead = DeadSocket()
        manager._rooms[retro_id].add(dead)

        resp = await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
        )
        assert resp.status_code == 201, "the write succeeded, so the response says so"
        assert (await live.event())["event"] == "cluster_created", "the live socket still got it"
        assert dead not in manager._rooms[retro_id], "the dead one was dropped"


@pytest.mark.asyncio
async def test_client_messages_are_not_commands(
    client, auth_headers, clustering_retro, alice_token
):
    """The socket is one-way. Anything sent up it is read and thrown away."""
    retro_id = clustering_retro["retro"]["id"]
    async with Socket(retro_id, alice_token) as socket:
        await socket._inbox.put({"type": "websocket.receive", "text": '{"event":"phase_changed"}'})
        await socket.silence()
        assert manager.connection_count(retro_id) == 1, "and it did not close the socket"

        resp = await client.post(
            f"/api/retros/{retro_id}/clusters", json={"name": "Flow"}, headers=auth_headers
        )
        assert resp.status_code == 201
        assert (await socket.event())["event"] == "cluster_created"


@pytest.fixture(autouse=True)
def _empty_rooms():
    """No test inherits another's sockets; the manager is module-level state."""
    manager._rooms.clear()
    yield
    manager._rooms.clear()


@pytest.mark.asyncio
async def test_a_stray_ballot_from_a_departed_member_does_not_appear_in_the_close(
    client, auth_headers, voting_retro, alice_token
):
    """`voting_closed` is the visible results shape, so it counts members, not ballots."""
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    await client.post(
        f"/api/retros/{retro_id}/votes", json={"cluster_ids": [flow]}, headers=auth_headers
    )

    from app.models.retro import Retrospective, Vote

    retro = await Retrospective.get(retro_id)
    retro.votes.append(Vote(user_id=PydanticObjectId(), cluster_ids=[flow]))
    await retro.save()

    async with Socket(retro_id, alice_token) as socket:
        await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "discuss"}, headers=auth_headers
        )
        closed = await socket.event()

    assert closed["event"] == "voting_closed"
    assert closed["data"]["members_voted"] == 1, "alice, and not the ghost ballot"
    assert closed["data"]["members_total"] == 2, "alice and bob"
    assert closed["data"]["total_votes"] == 2, "votes cast are counted, whoever cast them"
