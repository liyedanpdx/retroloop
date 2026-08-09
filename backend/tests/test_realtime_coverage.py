"""The events #12 deferred: votes, decisions and actions (#29).

Built on #12's socket harness, so the room, the envelope and the isolation rule
under test are the real ones. The assertion that matters most is the negative
one on `vote_submitted`: it says who voted and must never say what they chose.
"""

import json

import pytest

from tests.test_websocket import Socket

UNKNOWN_ID = "507f1f77bcf86cd799439011"


@pytest.fixture
async def alice_token(client, registered_user):
    resp = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "secret123"}
    )
    return resp.json()["access_token"]


@pytest.fixture
async def bob_token(client, second_user):
    resp = await client.post(
        "/api/auth/login", json={"email": "bob@example.com", "password": "secret456"}
    )
    return resp.json()["access_token"]


# --- votes --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_submitted_ballot_says_who_and_never_what(
    client, auth_headers, second_auth_headers, voting_retro, alice_token
):
    retro_id = voting_retro["retro"]["id"]
    flow, tooling, _ = [cluster["id"] for cluster in voting_retro["clusters"]]

    async with Socket(retro_id, alice_token) as socket:
        response = await client.post(
            f"/api/retros/{retro_id}/votes",
            json={"cluster_ids": [flow, flow, tooling]},
            headers=second_auth_headers,
        )
        assert response.status_code == 201, response.text

        event = await socket.event()

    assert event["event"] == "vote_submitted"
    assert set(event["data"]) == {"user_id"}
    # The whole point of #8, in one assertion.
    for leaked in (flow, tooling, "cluster_ids", "cluster_id"):
        assert leaked not in json.dumps(event), leaked


@pytest.mark.asyncio
async def test_a_rejected_ballot_says_nothing(
    client, auth_headers, second_auth_headers, voting_retro, alice_token
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]

    async with Socket(retro_id, alice_token) as socket:
        # 404: an unknown cluster id.
        assert (
            await client.post(
                f"/api/retros/{retro_id}/votes",
                json={"cluster_ids": ["nope"]},
                headers=second_auth_headers,
            )
        ).status_code == 404
        await socket.silence()

        # 422: an over-budget ballot.
        assert (
            await client.post(
                f"/api/retros/{retro_id}/votes",
                json={"cluster_ids": [flow] * 4},
                headers=second_auth_headers,
            )
        ).status_code == 422
        await socket.silence()

        # 409: a second ballot from the same member.
        await client.post(
            f"/api/retros/{retro_id}/votes",
            json={"cluster_ids": [flow]},
            headers=second_auth_headers,
        )
        assert (await socket.event())["event"] == "vote_submitted"
        assert (
            await client.post(
                f"/api/retros/{retro_id}/votes",
                json={"cluster_ids": [flow]},
                headers=second_auth_headers,
            )
        ).status_code == 409
        await socket.silence()


# --- decisions ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_decisions_are_announced_as_they_change(
    client, auth_headers, discussion_retro, alice_token
):
    retro_id = discussion_retro["retro"]["id"]

    async with Socket(retro_id, alice_token) as socket:
        created = await client.post(
            f"/api/retros/{retro_id}/decisions", json={"text": "Ship it"}, headers=auth_headers
        )
        assert created.status_code == 201, created.text
        event = await socket.event()
        assert event["event"] == "decision_created"
        assert event["data"] == created.json()

        decision_id = created.json()["id"]
        updated = await client.patch(
            f"/api/retros/{retro_id}/decisions/{decision_id}",
            json={"is_confirmed": True},
            headers=auth_headers,
        )
        event = await socket.event()
        assert event["event"] == "decision_updated"
        assert event["data"] == updated.json()
        assert event["data"]["is_confirmed"] is True

        deleted = await client.delete(
            f"/api/retros/{retro_id}/decisions/{decision_id}", headers=auth_headers
        )
        assert deleted.status_code == 204
        assert await socket.event() == {
            "event": "decision_deleted",
            "data": {"id": decision_id},
        }


# --- actions ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actions_are_announced_as_they_change(
    client, auth_headers, second_user, discussion_retro, alice_token
):
    retro_id = discussion_retro["retro"]["id"]

    async with Socket(retro_id, alice_token) as socket:
        created = await client.post(
            f"/api/retros/{retro_id}/actions",
            json={"description": "Write it up", "owner_id": second_user["id"]},
            headers=auth_headers,
        )
        assert created.status_code == 201, created.text
        event = await socket.event()
        assert event["event"] == "action_created"
        assert event["data"] == created.json()
        assert event["data"]["owner_state"] == "assigned", "#23's field travels too"

        action_id = created.json()["id"]
        updated = await client.patch(
            f"/api/retros/{retro_id}/actions/{action_id}",
            json={"status": "done"},
            headers=auth_headers,
        )
        event = await socket.event()
        assert event["event"] == "action_updated"
        assert event["data"] == updated.json()

        assert (
            await client.delete(
                f"/api/retros/{retro_id}/actions/{action_id}", headers=auth_headers
            )
        ).status_code == 204
        assert await socket.event() == {"event": "action_deleted", "data": {"id": action_id}}


@pytest.mark.asyncio
async def test_an_owner_editing_their_own_action_is_announced_too(
    client, auth_headers, second_auth_headers, second_user, discussion_retro, alice_token
):
    """The facilitator is watching the board while somebody ticks their item off."""
    retro_id = discussion_retro["retro"]["id"]
    created = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Bob's job", "owner_id": second_user["id"]},
        headers=auth_headers,
    )

    async with Socket(retro_id, alice_token) as socket:
        updated = await client.patch(
            f"/api/retros/{retro_id}/actions/{created.json()['id']}",
            json={"status": "done"},
            headers=second_auth_headers,
        )
        assert updated.status_code == 200, updated.text
        event = await socket.event()

    assert event["event"] == "action_updated"
    assert event["data"]["status"] == "done"


# --- what must not be announced ------------------------------------------------


@pytest.mark.asyncio
async def test_refused_discussion_mutations_say_nothing(
    client, auth_headers, second_auth_headers, outsider_auth_headers, discussion_retro,
    alice_token,
):
    retro_id = discussion_retro["retro"]["id"]

    async with Socket(retro_id, alice_token) as socket:
        # 403 — a member cannot create a decision.
        assert (
            await client.post(
                f"/api/retros/{retro_id}/decisions",
                json={"text": "Mine"},
                headers=second_auth_headers,
            )
        ).status_code == 403
        await socket.silence()

        # 403 — an outsider cannot create an action.
        assert (
            await client.post(
                f"/api/retros/{retro_id}/actions",
                json={"description": "Mine"},
                headers=outsider_auth_headers,
            )
        ).status_code == 403
        await socket.silence()

        # 422 — a blank decision never reaches the handler.
        assert (
            await client.post(
                f"/api/retros/{retro_id}/decisions", json={"text": "   "}, headers=auth_headers
            )
        ).status_code == 422
        await socket.silence()

        # 404 — deleting something that is not there.
        assert (
            await client.delete(
                f"/api/retros/{retro_id}/actions/nope", headers=auth_headers
            )
        ).status_code == 404
        await socket.silence()


@pytest.mark.asyncio
async def test_another_retro_hears_none_of_it(
    client, auth_headers, discussion_retro, reveal, advance_phase, alice_token
):
    second = await client.post(
        "/api/projects", json={"name": "Team Beta", "description": None}, headers=auth_headers
    )
    other_cycle = await client.post(
        f"/api/projects/{second.json()['id']}/cycles", headers=auth_headers
    )
    other_retro = await reveal(other_cycle.json()["id"])

    retro_id = discussion_retro["retro"]["id"]
    async with (
        Socket(retro_id, alice_token) as here,
        Socket(other_retro["id"], alice_token) as elsewhere,
    ):
        await client.post(
            f"/api/retros/{retro_id}/decisions", json={"text": "Ship it"}, headers=auth_headers
        )
        assert (await here.event())["event"] == "decision_created"
        await elsewhere.silence()


@pytest.mark.asyncio
async def test_every_new_event_uses_the_same_envelope(
    client, auth_headers, discussion_retro, alice_token
):
    retro_id = discussion_retro["retro"]["id"]

    async with Socket(retro_id, alice_token) as socket:
        await client.post(
            f"/api/retros/{retro_id}/decisions", json={"text": "Ship it"}, headers=auth_headers
        )
        await client.post(
            f"/api/retros/{retro_id}/actions",
            json={"description": "Write it up"},
            headers=auth_headers,
        )

        for _ in range(2):
            event = await socket.event()
            assert set(event) == {"event", "data"}
            assert isinstance(event["data"], dict)
            assert "token" not in json.dumps(event)
