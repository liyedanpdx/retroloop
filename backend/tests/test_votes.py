import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.retro import MAX_VOTES_PER_USER, Retrospective

UNKNOWN_ID = "507f1f77bcf86cd799439011"
UNKNOWN_CLUSTER_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


async def _vote(client, retro_id, cluster_ids, headers=None):
    """Submit a ballot. `headers=None` sends no Authorization header at all."""
    kwargs = {} if headers is None else {"headers": headers}
    return await client.post(
        f"/api/retros/{retro_id}/votes", json={"cluster_ids": cluster_ids}, **kwargs
    )


async def _results(client, retro_id, headers=None):
    kwargs = {} if headers is None else {"headers": headers}
    return await client.get(f"/api/retros/{retro_id}/votes/results", **kwargs)


async def _ballots(retro_id):
    """The ballots as the database holds them, not as a response body claims."""
    stored = await Retrospective.get(retro_id)
    return stored.votes


async def _pin_created_at(retro_id, offsets):
    """Pin clusters' `created_at`, given `{cluster_id: minutes}`.

    Clusters made through the API land milliseconds apart in creation order,
    which is enough for the ordering to be correct but not enough for a test to
    prove *why*. Writing the timestamps directly lets a test build a deliberate
    tie, and deliberately fight the id tie-break.
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stored = await Retrospective.get(retro_id)
    for cluster in stored.clusters:
        if cluster.id in offsets:
            cluster.created_at = base + timedelta(minutes=offsets[cluster.id])
    await stored.save()


# --- POST /api/retros/{id}/votes -------------------------------------------


@pytest.mark.asyncio
async def test_submit_three_votes(client, auth_headers, registered_user, voting_retro):
    retro = voting_retro["retro"]
    ids = [c["id"] for c in voting_retro["clusters"]]

    resp = await _vote(client, retro["id"], ids, auth_headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["user_id"] == registered_user["id"]
    assert data["cluster_ids"] == ids
    assert data["submitted_at"] is not None

    ballots = await _ballots(retro["id"])
    assert len(ballots) == 1, "the ballot appears exactly once in retro.votes"
    assert str(ballots[0].user_id) == registered_user["id"]
    assert ballots[0].cluster_ids == ids
    assert ballots[0].submitted_at is not None


@pytest.mark.asyncio
async def test_submit_one_vote(client, auth_headers, voting_retro):
    """The budget is "up to 3", not "exactly 3"."""
    retro = voting_retro["retro"]
    first = voting_retro["clusters"][0]["id"]

    resp = await _vote(client, retro["id"], [first], auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["cluster_ids"] == [first]
    assert (await _ballots(retro["id"]))[0].cluster_ids == [first]


@pytest.mark.asyncio
async def test_submit_two_votes(client, auth_headers, voting_retro):
    retro = voting_retro["retro"]
    ids = [c["id"] for c in voting_retro["clusters"][:2]]

    resp = await _vote(client, retro["id"], ids, auth_headers)
    assert resp.status_code == 201, resp.text
    assert (await _ballots(retro["id"]))[0].cluster_ids == ids


@pytest.mark.asyncio
async def test_stacked_votes_count_separately(
    client, auth_headers, second_auth_headers, voting_retro
):
    """Three votes on one cluster is a weight of 3, not of 1."""
    retro = voting_retro["retro"]
    flow, tooling, _ = [c["id"] for c in voting_retro["clusters"]]

    resp = await _vote(client, retro["id"], [flow, flow, flow], auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["cluster_ids"] == [flow, flow, flow]
    assert (await _ballots(retro["id"]))[0].cluster_ids == [flow, flow, flow]

    # Bob votes too, which opens the results and lets the count be read back.
    assert (await _vote(client, retro["id"], [tooling], second_auth_headers)).status_code == 201

    results = await _results(client, retro["id"], auth_headers)
    assert results.status_code == 200, results.text
    counts = {row["cluster_id"]: row["vote_count"] for row in results.json()["results"]}
    assert counts[flow] == 3
    assert results.json()["total_votes"] == 4


@pytest.mark.asyncio
async def test_empty_ballot_is_rejected(client, auth_headers, voting_retro):
    """Indistinguishable from not voting, and with no retraction it would stick."""
    retro = voting_retro["retro"]

    resp = await _vote(client, retro["id"], [], auth_headers)
    assert resp.status_code == 422, resp.text
    assert await _ballots(retro["id"]) == []


@pytest.mark.asyncio
async def test_more_than_three_votes_is_rejected(client, auth_headers, voting_retro):
    retro = voting_retro["retro"]
    ids = [c["id"] for c in voting_retro["clusters"]]
    over_budget = ids + [ids[0]]
    assert len(over_budget) == MAX_VOTES_PER_USER + 1

    resp = await _vote(client, retro["id"], over_budget, auth_headers)
    assert resp.status_code == 422, resp.text
    assert await _ballots(retro["id"]) == [], "no ballot is written"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"cluster_ids": None}, {"cluster_ids": [1]}])
async def test_malformed_ballot_bodies_are_rejected(client, auth_headers, voting_retro, body):
    retro = voting_retro["retro"]

    resp = await client.post(
        f"/api/retros/{retro['id']}/votes", json=body, headers=auth_headers
    )
    assert resp.status_code == 422, resp.text
    assert await _ballots(retro["id"]) == []


@pytest.mark.asyncio
async def test_unknown_cluster_id_is_rejected(client, auth_headers, voting_retro):
    retro = voting_retro["retro"]

    resp = await _vote(client, retro["id"], [UNKNOWN_CLUSTER_ID], auth_headers)
    assert resp.status_code == 404, resp.text
    assert await _ballots(retro["id"]) == []


@pytest.mark.asyncio
async def test_a_ballot_mixing_known_and_unknown_ids_writes_nothing(
    client, auth_headers, voting_retro
):
    """All or nothing — the real vote does not survive the unknown one."""
    retro = voting_retro["retro"]
    real = voting_retro["clusters"][0]["id"]

    resp = await _vote(client, retro["id"], [real, UNKNOWN_CLUSTER_ID], auth_headers)
    assert resp.status_code == 404, resp.text
    assert await _ballots(retro["id"]) == []


@pytest.mark.asyncio
async def test_voting_on_a_retro_with_no_clusters(
    client, auth_headers, clustering_retro, advance_phase
):
    retro = await advance_phase(clustering_retro["retro"]["id"], "vote")
    assert retro["clusters"] == []

    resp = await _vote(client, retro["id"], [UNKNOWN_CLUSTER_ID], auth_headers)
    assert resp.status_code == 404, resp.text
    assert await _ballots(retro["id"]) == []


@pytest.mark.asyncio
async def test_voting_outside_the_vote_phase(
    client, auth_headers, clustering_retro, add_cluster, advance_phase
):
    """Checked in the phase before the vote phase and the one after it."""
    retro = clustering_retro["retro"]
    cluster = await add_cluster(retro["id"], name="Flow")

    in_cluster = await _vote(client, retro["id"], [cluster["id"]], auth_headers)
    assert in_cluster.status_code == 400, in_cluster.text

    await advance_phase(retro["id"], "vote")
    await advance_phase(retro["id"], "discuss")

    in_discuss = await _vote(client, retro["id"], [cluster["id"]], auth_headers)
    assert in_discuss.status_code == 400, in_discuss.text

    # Phase is checked before the cluster lookup, so this is 400 and not 404.
    unknown_cluster = await _vote(client, retro["id"], [UNKNOWN_CLUSTER_ID], auth_headers)
    assert unknown_cluster.status_code == 400, unknown_cluster.text

    assert await _ballots(retro["id"]) == []


@pytest.mark.asyncio
async def test_a_second_submission_is_refused(client, auth_headers, voting_retro):
    retro = voting_retro["retro"]
    flow, tooling, meetings = [c["id"] for c in voting_retro["clusters"]]

    first = await _vote(client, retro["id"], [flow], auth_headers)
    assert first.status_code == 201, first.text
    original = (await _ballots(retro["id"]))[0]

    second = await _vote(client, retro["id"], [tooling, meetings], auth_headers)
    assert second.status_code == 409, second.text

    ballots = await _ballots(retro["id"])
    assert len(ballots) == 1
    assert ballots[0].cluster_ids == original.cluster_ids == [flow]
    assert ballots[0].submitted_at == original.submitted_at


@pytest.mark.asyncio
async def test_a_second_submission_with_unknown_ids_is_still_a_conflict(
    client, auth_headers, voting_retro
):
    """The already-voted check runs before the cluster lookup, so 409 beats 404."""
    retro = voting_retro["retro"]
    flow = voting_retro["clusters"][0]["id"]
    assert (await _vote(client, retro["id"], [flow], auth_headers)).status_code == 201

    resp = await _vote(client, retro["id"], [UNKNOWN_CLUSTER_ID], auth_headers)
    assert resp.status_code == 409, resp.text
    assert [b.cluster_ids for b in await _ballots(retro["id"])] == [[flow]]


@pytest.mark.asyncio
async def test_another_member_can_vote_after_the_first(
    client, auth_headers, second_auth_headers, registered_user, second_user, voting_retro
):
    retro = voting_retro["retro"]
    flow, tooling, _ = [c["id"] for c in voting_retro["clusters"]]

    assert (await _vote(client, retro["id"], [flow], auth_headers)).status_code == 201

    resp = await _vote(client, retro["id"], [tooling], second_auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_id"] == second_user["id"]

    ballots = await _ballots(retro["id"])
    assert {str(b.user_id) for b in ballots} == {registered_user["id"], second_user["id"]}


@pytest.mark.asyncio
async def test_any_member_can_vote_not_only_the_facilitator(
    client, second_auth_headers, second_user, voting_retro
):
    retro = voting_retro["retro"]
    flow = voting_retro["clusters"][0]["id"]

    resp = await _vote(client, retro["id"], [flow], second_auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_id"] == second_user["id"]


@pytest.mark.asyncio
async def test_non_members_cannot_vote(client, outsider_auth_headers, voting_retro):
    """Membership is checked first, so 403 wins over anything else that is wrong."""
    retro = voting_retro["retro"]
    flow = voting_retro["clusters"][0]["id"]

    valid = await _vote(client, retro["id"], [flow], outsider_auth_headers)
    assert valid.status_code == 403, valid.text

    unknown_cluster = await _vote(client, retro["id"], [UNKNOWN_CLUSTER_ID], outsider_auth_headers)
    assert unknown_cluster.status_code == 403, "403, not the 404 a member would get"

    assert await _ballots(retro["id"]) == []


# --- GET /api/retros/{id}/votes/results -------------------------------------


@pytest.mark.asyncio
async def test_results_are_hidden_while_a_member_has_not_voted(
    client, auth_headers, voting_retro
):
    retro = voting_retro["retro"]

    assert (await _results(client, retro["id"], auth_headers)).status_code == 409

    flow = voting_retro["clusters"][0]["id"]
    assert (await _vote(client, retro["id"], [flow], auth_headers)).status_code == 201

    still_hidden = await _results(client, retro["id"], auth_headers)
    assert still_hidden.status_code == 409, "bob has not voted yet"


@pytest.mark.asyncio
async def test_results_are_hidden_in_the_reveal_and_cluster_phases(
    client, auth_headers, shared_cycle, add_card, reveal, advance_phase
):
    await add_card(shared_cycle["id"])
    retro = await reveal(shared_cycle["id"])

    in_reveal = await _results(client, retro["id"], auth_headers)
    assert in_reveal.status_code == 409, in_reveal.text

    await advance_phase(retro["id"], "cluster")
    in_cluster = await _results(client, retro["id"], auth_headers)
    assert in_cluster.status_code == 409, in_cluster.text


@pytest.mark.asyncio
async def test_results_open_when_the_facilitator_advances_to_discuss(
    client, auth_headers, voting_retro, advance_phase
):
    """Nobody voted, and the results are still readable — advancing closes voting."""
    retro = voting_retro["retro"]
    await advance_phase(retro["id"], "discuss")

    resp = await _results(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["members_voted"] == 0
    assert data["total_votes"] == 0
    assert [row["vote_count"] for row in data["results"]] == [0, 0, 0]

    await advance_phase(retro["id"], "done")
    in_done = await _results(client, retro["id"], auth_headers)
    assert in_done.status_code == 200, in_done.text


@pytest.mark.asyncio
async def test_results_open_once_every_member_has_voted(
    client, auth_headers, second_auth_headers, voting_retro
):
    """Still in the vote phase — nothing auto-advances when the last member votes."""
    retro = voting_retro["retro"]
    flow, tooling, _ = [c["id"] for c in voting_retro["clusters"]]

    assert (await _vote(client, retro["id"], [flow], auth_headers)).status_code == 201
    assert (await _vote(client, retro["id"], [tooling], second_auth_headers)).status_code == 201

    resp = await _results(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text

    stored = await Retrospective.get(retro["id"])
    assert stored.phase == "vote", "voting closing does not move the phase"


@pytest.mark.asyncio
async def test_results_include_clusters_nobody_voted_for(
    client, auth_headers, second_auth_headers, voting_retro
):
    retro = voting_retro["retro"]
    flow, tooling, meetings = [c["id"] for c in voting_retro["clusters"]]

    assert (await _vote(client, retro["id"], [flow], auth_headers)).status_code == 201
    assert (await _vote(client, retro["id"], [flow], second_auth_headers)).status_code == 201

    resp = await _results(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    rows = {row["cluster_id"]: row for row in resp.json()["results"]}
    assert set(rows) == {flow, tooling, meetings}, "every cluster gets a row"
    assert rows[tooling]["vote_count"] == 0
    assert rows[meetings]["vote_count"] == 0
    assert rows[tooling]["name"] == "Tooling"


@pytest.mark.asyncio
async def test_results_ordering_and_tie_break(
    client, auth_headers, second_auth_headers, clustering_retro, add_cluster, advance_phase
):
    """`vote_count` desc, then `created_at` asc, then `id` asc — and it repeats.

    The three tied clusters are timestamped *against* their id order: the one
    with the largest id is made the oldest. Sorting on id alone would therefore
    put it last instead of first, so this pins `created_at` as the outer
    tie-break rather than letting a random UUID happen to agree with it.
    """
    retro = clustering_retro["retro"]
    tied = [await add_cluster(retro["id"], name=n) for n in ("Flow", "Tooling", "Meetings")]
    empty = await add_cluster(retro["id"], name="Nobody voted for this")

    low, mid, high = sorted((c["id"] for c in tied))
    await _pin_created_at(retro["id"], {high: 0, low: 5, mid: 5, empty["id"]: 9})
    await advance_phase(retro["id"], "vote")

    first = await _vote(client, retro["id"], [low, low, mid], auth_headers)
    assert first.status_code == 201, first.text
    second = await _vote(client, retro["id"], [mid, high, high], second_auth_headers)
    assert second.status_code == 201, second.text

    resp = await _results(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["results"]

    assert [row["vote_count"] for row in rows] == [2, 2, 2, 0]
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]
    # `high` is oldest so it leads despite the largest id; `low` and `mid` share
    # a timestamp and are separated by id; the zero-vote cluster sinks to last.
    assert [row["cluster_id"] for row in rows] == [high, low, mid, empty["id"]]

    again = await _results(client, retro["id"], auth_headers)
    assert again.json()["results"] == rows, "two calls in a row agree"


@pytest.mark.asyncio
async def test_results_counts_and_member_totals(
    client, auth_headers, second_auth_headers, voting_retro
):
    retro = voting_retro["retro"]
    flow, tooling, meetings = [c["id"] for c in voting_retro["clusters"]]

    await _vote(client, retro["id"], [flow, flow, tooling], auth_headers)
    await _vote(client, retro["id"], [flow, meetings], second_auth_headers)

    resp = await _results(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["members_voted"] == 2
    assert data["members_total"] == 2
    assert data["total_votes"] == 5, "stacked votes each count"
    counts = {row["cluster_id"]: row["vote_count"] for row in data["results"]}
    assert counts == {flow: 3, tooling: 1, meetings: 1}


@pytest.mark.asyncio
async def test_results_never_say_who_voted_for_what(
    client, auth_headers, second_auth_headers, registered_user, second_user, voting_retro
):
    retro = voting_retro["retro"]
    flow, tooling, _ = [c["id"] for c in voting_retro["clusters"]]
    await _vote(client, retro["id"], [flow], auth_headers)
    await _vote(client, retro["id"], [tooling], second_auth_headers)

    resp = await _results(client, retro["id"], auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    for row in body["results"]:
        assert set(row) == {"cluster_id", "name", "vote_count", "rank"}

    raw = json.dumps(body)
    assert registered_user["id"] not in raw
    assert second_user["id"] not in raw


@pytest.mark.asyncio
async def test_non_members_cannot_read_results(
    client, auth_headers, second_auth_headers, outsider_auth_headers, voting_retro
):
    """403 for an outsider even once the results are open — distinct from the 409."""
    retro = voting_retro["retro"]
    flow = voting_retro["clusters"][0]["id"]

    hidden = await _results(client, retro["id"], outsider_auth_headers)
    assert hidden.status_code == 403, hidden.text

    await _vote(client, retro["id"], [flow], auth_headers)
    await _vote(client, retro["id"], [flow], second_auth_headers)

    still_forbidden = await _results(client, retro["id"], outsider_auth_headers)
    assert still_forbidden.status_code == 403, still_forbidden.text


# --- Cross-cutting -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_retro_payload_never_carries_cluster_ids(
    client, auth_headers, second_auth_headers, registered_user, voting_retro, advance_phase
):
    retro = voting_retro["retro"]
    flow, tooling, _ = [c["id"] for c in voting_retro["clusters"]]
    await _vote(client, retro["id"], [flow, flow], auth_headers)
    await _vote(client, retro["id"], [tooling], second_auth_headers)

    during = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    assert during.status_code == 200, during.text
    votes = during.json()["votes"]
    assert len(votes) == 2
    for vote in votes:
        assert set(vote) == {"user_id", "submitted_at"}
    assert registered_user["id"] in [v["user_id"] for v in votes]
    assert "cluster_ids" not in json.dumps(during.json()["votes"])

    await advance_phase(retro["id"], "discuss")
    after = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    assert after.status_code == 200, after.text
    for vote in after.json()["votes"]:
        assert set(vote) == {"user_id", "submitted_at"}


@pytest.mark.asyncio
async def test_unknown_and_malformed_retro_ids(client, auth_headers, voting_retro):
    flow = voting_retro["clusters"][0]["id"]
    cases = [
        await _vote(client, UNKNOWN_ID, [flow], auth_headers),
        await _vote(client, "abc", [flow], auth_headers),
        await _results(client, UNKNOWN_ID, auth_headers),
        await _results(client, "abc", auth_headers),
    ]
    for resp in cases:
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_both_endpoints_require_authentication(client, voting_retro):
    """HTTPBearer rejects before the handler runs, so this is 401 and not 403."""
    retro = voting_retro["retro"]
    flow = voting_retro["clusters"][0]["id"]

    assert (await _vote(client, retro["id"], [flow])).status_code == 401
    assert (await _results(client, retro["id"])).status_code == 401
