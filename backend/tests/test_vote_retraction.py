"""Withdrawing a ballot, and the boundary that closes it forever (#21).

The rule the whole issue turns on is that results visibility is monotonic. Once
the tally could have been seen by anybody, it stays visible and withdrawal is
over — no membership change re-opens either. Several tests here exist only to
prove that a member being added or removed cannot move that line.
"""

import asyncio

import pytest

from app.models.retro import Retrospective
from tests.test_websocket import Socket

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"
ALREADY_OPEN = "Voting results are already open"
NO_BALLOT = "You have not voted in this retrospective"


def _url(retro_id: str) -> str:
    return f"/api/retros/{retro_id}/votes"


@pytest.fixture
async def alice_token(client, registered_user):
    resp = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "secret123"}
    )
    return resp.json()["access_token"]


async def _vote(client, retro_id, headers, cluster_ids):
    response = await client.post(_url(retro_id), json={"cluster_ids": cluster_ids}, headers=headers)
    assert response.status_code == 201, response.text
    return response


# --- when results open --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_partial_vote_does_not_open_them(client, auth_headers, voting_retro):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]

    await _vote(client, retro_id, auth_headers, [flow])

    stored = await Retrospective.get(retro_id)
    assert stored.voting_results_opened_at is None, "bob has not voted"
    assert (
        await client.get(f"{_url(retro_id)}/results", headers=auth_headers)
    ).status_code == 409


@pytest.mark.asyncio
async def test_the_last_ballot_opens_them_once_and_stamps_it(
    client, auth_headers, second_auth_headers, voting_retro
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]

    await _vote(client, retro_id, auth_headers, [flow])
    await _vote(client, retro_id, second_auth_headers, [flow])

    stored = await Retrospective.get(retro_id)
    assert stored.voting_results_opened_at is not None
    assert (
        await client.get(f"{_url(retro_id)}/results", headers=auth_headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_advancing_out_of_vote_opens_them(
    client, auth_headers, voting_retro, advance_phase
):
    retro_id = voting_retro["retro"]["id"]
    assert (await Retrospective.get(retro_id)).voting_results_opened_at is None

    await advance_phase(retro_id, "discuss")

    assert (await Retrospective.get(retro_id)).voting_results_opened_at is not None


@pytest.mark.asyncio
async def test_the_stamp_never_moves_or_clears(
    client, auth_headers, second_auth_headers, voting_retro, advance_phase
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    await _vote(client, retro_id, auth_headers, [flow])
    await _vote(client, retro_id, second_auth_headers, [flow])
    first = (await Retrospective.get(retro_id)).voting_results_opened_at

    await advance_phase(retro_id, "discuss")

    assert (await Retrospective.get(retro_id)).voting_results_opened_at == first


@pytest.mark.asyncio
async def test_removing_the_last_non_voter_does_not_publish_the_tally(
    client, auth_headers, second_user, project_with_member, voting_retro
):
    """The bug the stored stamp exists to prevent."""
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    await _vote(client, retro_id, auth_headers, [flow])

    removed = await client.delete(
        f"/api/projects/{project_with_member['id']}/members/{second_user['id']}",
        headers=auth_headers,
    )
    assert removed.status_code == 200, removed.text

    assert (await Retrospective.get(retro_id)).voting_results_opened_at is None
    assert (
        await client.get(f"{_url(retro_id)}/results", headers=auth_headers)
    ).status_code == 409, "nobody chose to close voting"
    # And the facilitator can still advance normally.
    assert (
        await client.patch(
            f"/api/retros/{retro_id}/phase", json={"phase": "discuss"}, headers=auth_headers
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_adding_a_member_afterwards_does_not_hide_them_again(
    client, auth_headers, second_auth_headers, project_with_member, voting_retro
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    await _vote(client, retro_id, auth_headers, [flow])
    await _vote(client, retro_id, second_auth_headers, [flow])

    await client.post(
        "/api/auth/register",
        json={"email": "carol@example.com", "password": "secret789", "display_name": "Carol"},
    )
    added = await client.post(
        f"/api/projects/{project_with_member['id']}/members",
        json={"email": "carol@example.com"},
        headers=auth_headers,
    )
    assert added.status_code == 201, added.text

    assert (
        await client.get(f"{_url(retro_id)}/results", headers=auth_headers)
    ).status_code == 200, "visibility is monotonic"


# --- withdrawing --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_whole_ballot_goes_and_the_counts_follow(
    client, auth_headers, second_auth_headers, voting_retro
):
    retro_id = voting_retro["retro"]["id"]
    flow, tooling, _ = [cluster["id"] for cluster in voting_retro["clusters"]]
    await _vote(client, retro_id, auth_headers, [flow, flow, tooling])
    await _vote(client, retro_id, second_auth_headers, [flow])
    # Bob's ballot completed the membership, so results opened — undo that for
    # this test by removing only alice's, which is what she is entitled to do.
    stored = await Retrospective.get(retro_id)
    stored.voting_results_opened_at = None
    await stored.save()

    response = await client.delete(_url(retro_id), headers=auth_headers)

    assert response.status_code == 204
    assert response.content == b""
    after = await Retrospective.get(retro_id)
    assert [str(ballot.user_id) for ballot in after.votes] != []
    assert len(after.votes) == 1, "only the caller's ballot"
    assert after.votes[0].cluster_ids == [flow], "bob's is byte-for-byte untouched"


@pytest.mark.asyncio
async def test_the_member_may_then_submit_a_fresh_ballot(
    client, auth_headers, voting_retro
):
    retro_id = voting_retro["retro"]["id"]
    flow, tooling, _ = [cluster["id"] for cluster in voting_retro["clusters"]]
    first = await _vote(client, retro_id, auth_headers, [flow, flow])

    assert (await client.delete(_url(retro_id), headers=auth_headers)).status_code == 204

    second = await _vote(client, retro_id, auth_headers, [tooling])
    assert second.json()["cluster_ids"] == [tooling]
    assert second.json()["submitted_at"] != first.json()["submitted_at"]

    stored = await Retrospective.get(retro_id)
    assert len(stored.votes) == 1, "one ballot, not two"


@pytest.mark.asyncio
async def test_withdrawing_twice_is_a_404(client, auth_headers, voting_retro):
    retro_id = voting_retro["retro"]["id"]
    await _vote(client, retro_id, auth_headers, [voting_retro["clusters"][0]["id"]])

    assert (await client.delete(_url(retro_id), headers=auth_headers)).status_code == 204
    second = await client.delete(_url(retro_id), headers=auth_headers)
    assert second.status_code == 404
    assert second.json()["detail"] == NO_BALLOT


@pytest.mark.asyncio
async def test_a_member_who_never_voted_gets_404(client, second_auth_headers, voting_retro):
    response = await client.delete(_url(voting_retro["retro"]["id"]), headers=second_auth_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == NO_BALLOT


@pytest.mark.asyncio
async def test_it_closes_once_results_have_opened(
    client, auth_headers, second_auth_headers, voting_retro
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    await _vote(client, retro_id, auth_headers, [flow])
    await _vote(client, retro_id, second_auth_headers, [flow])

    response = await client.delete(_url(retro_id), headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == ALREADY_OPEN
    assert len((await Retrospective.get(retro_id)).votes) == 2, "nothing was removed"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["reveal", "cluster", "discuss", "done"])
async def test_the_wrong_phase_wins_over_everything_else(
    client, auth_headers, voting_retro, phase
):
    retro_id = voting_retro["retro"]["id"]
    await _vote(client, retro_id, auth_headers, [voting_retro["clusters"][0]["id"]])

    retro = await Retrospective.get(retro_id)
    retro.phase = phase
    await retro.save()

    response = await client.delete(_url(retro_id), headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"] not in (ALREADY_OPEN, NO_BALLOT)
    assert len((await Retrospective.get(retro_id)).votes) == 1


@pytest.mark.asyncio
async def test_a_closed_cycle_wins_over_the_ballot_state(
    client, auth_headers, voting_retro
):
    retro_id = voting_retro["retro"]["id"]
    await _vote(client, retro_id, auth_headers, [voting_retro["clusters"][0]["id"]])
    await client.patch(
        f"/api/cycles/{voting_retro['cycle']['id']}", json={"status": "closed"},
        headers=auth_headers,
    )

    response = await client.delete(_url(retro_id), headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"] == "The retrospective's cycle is closed"
    assert len((await Retrospective.get(retro_id)).votes) == 1


@pytest.mark.asyncio
async def test_permission_and_id_checks_come_first(
    client, auth_headers, outsider_auth_headers, voting_retro
):
    retro_id = voting_retro["retro"]["id"]

    assert (await client.delete(_url(retro_id))).status_code == 401
    assert (
        await client.delete(_url(retro_id), headers=outsider_auth_headers)
    ).status_code == 403
    assert (await client.delete(_url(UNKNOWN_ID), headers=auth_headers)).status_code == 404
    assert (await client.delete(_url(MALFORMED_ID), headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_nobody_can_withdraw_somebody_elses_ballot(
    client, auth_headers, second_auth_headers, voting_retro
):
    """There is no ballot id in the URL, and a facilitator has no extra reach."""
    retro_id = voting_retro["retro"]["id"]
    await _vote(client, retro_id, second_auth_headers, [voting_retro["clusters"][0]["id"]])

    # Alice is the facilitator and has no ballot of her own.
    response = await client.delete(_url(retro_id), headers=auth_headers)

    assert response.status_code == 404
    assert len((await Retrospective.get(retro_id)).votes) == 1, "bob's ballot is still there"


@pytest.mark.asyncio
async def test_two_simultaneous_withdrawals_produce_one_204(
    client, auth_headers, voting_retro
):
    """The conditional write is what makes this true, not the read before it."""
    retro_id = voting_retro["retro"]["id"]
    await _vote(client, retro_id, auth_headers, [voting_retro["clusters"][0]["id"]])

    first, second = await asyncio.gather(
        client.delete(_url(retro_id), headers=auth_headers),
        client.delete(_url(retro_id), headers=auth_headers),
    )

    assert sorted([first.status_code, second.status_code]) == [204, 404]
    assert (await Retrospective.get(retro_id)).votes == []


# --- events -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_withdrawal_is_announced_without_the_choices(
    client, auth_headers, voting_retro, alice_token
):
    retro_id = voting_retro["retro"]["id"]
    flow, tooling, _ = [cluster["id"] for cluster in voting_retro["clusters"]]
    await _vote(client, retro_id, auth_headers, [flow, tooling])

    async with Socket(retro_id, alice_token) as socket:
        assert (await client.delete(_url(retro_id), headers=auth_headers)).status_code == 204
        event = await socket.event()

    assert event["event"] == "vote_retracted"
    assert set(event["data"]) == {"user_id"}
    for leaked in (flow, tooling, "cluster_ids"):
        assert leaked not in str(event), leaked


@pytest.mark.asyncio
async def test_a_refused_withdrawal_announces_nothing(
    client, auth_headers, second_auth_headers, voting_retro, alice_token
):
    retro_id = voting_retro["retro"]["id"]

    async with Socket(retro_id, alice_token) as socket:
        # 404 — alice has no ballot.
        assert (await client.delete(_url(retro_id), headers=auth_headers)).status_code == 404
        await socket.silence()


@pytest.mark.asyncio
async def test_the_ballot_that_opens_results_announces_the_aggregate(
    client, auth_headers, second_auth_headers, voting_retro, alice_token
):
    retro_id = voting_retro["retro"]["id"]
    flow = voting_retro["clusters"][0]["id"]
    await _vote(client, retro_id, auth_headers, [flow])

    async with Socket(retro_id, alice_token) as socket:
        await _vote(client, retro_id, second_auth_headers, [flow])

        submitted = await socket.event()
        closed = await socket.event()

    assert submitted["event"] == "vote_submitted"
    assert closed["event"] == "voting_closed"
    assert set(closed["data"]) == {"members_voted", "members_total", "total_votes", "results"}
    assert "cluster_ids" not in str(closed)
