"""Discussion phase: topic generation, topic status, decisions and actions (#9).

Two conventions carried over from `tests/test_votes.py`. Every helper takes
`headers=None` meaning *no* `Authorization` header at all, so the 401 cases are
the same call with one argument dropped. And a mutation that should not have
happened is checked by reading the retro back out of the database, not by
trusting the response body that refused it.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.models.retro import Cluster, Retrospective, Topic, Vote
from app.services.discussion import create_topics
from app.services.votes import tally

UNKNOWN_ID = "507f1f77bcf86cd799439011"
UNKNOWN_UUID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
MALFORMED_ID = "abc"


# --- helpers -----------------------------------------------------------------


def _kwargs(headers):
    return {} if headers is None else {"headers": headers}


async def _patch_topic(client, retro_id, topic_id, body, headers=None):
    return await client.patch(
        f"/api/retros/{retro_id}/topics/{topic_id}", json=body, **_kwargs(headers)
    )


async def _post_decision(client, retro_id, body, headers=None):
    return await client.post(
        f"/api/retros/{retro_id}/decisions", json=body, **_kwargs(headers)
    )


async def _patch_decision(client, retro_id, decision_id, body, headers=None):
    return await client.patch(
        f"/api/retros/{retro_id}/decisions/{decision_id}", json=body, **_kwargs(headers)
    )


async def _delete_decision(client, retro_id, decision_id, headers=None):
    return await client.delete(
        f"/api/retros/{retro_id}/decisions/{decision_id}", **_kwargs(headers)
    )


async def _post_action(client, retro_id, body, headers=None):
    return await client.post(f"/api/retros/{retro_id}/actions", json=body, **_kwargs(headers))


async def _patch_action(client, retro_id, action_id, body, headers=None):
    return await client.patch(
        f"/api/retros/{retro_id}/actions/{action_id}", json=body, **_kwargs(headers)
    )


async def _delete_action(client, retro_id, action_id, headers=None):
    return await client.delete(
        f"/api/retros/{retro_id}/actions/{action_id}", **_kwargs(headers)
    )


async def _stored(retro_id) -> Retrospective:
    """The retro as the database holds it, not as a response body claims."""
    return await Retrospective.get(retro_id)


def _build_retro(cluster_ids, ballots=(), same_timestamp=True) -> Retrospective:
    """A retro assembled in memory, with cluster ids and timestamps under control.

    Never inserted. `create_topics` and `tally` are both synchronous and take a
    plain document, which is the whole point of putting them in a service — a
    test can pin an id collision that the API's random UUIDs could never produce.
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Retrospective(
        cycle_id=PydanticObjectId(),
        clusters=[
            Cluster(
                id=cluster_id,
                name=f"cluster {cluster_id}",
                created_at=base if same_timestamp else base + timedelta(minutes=offset),
            )
            for offset, cluster_id in enumerate(cluster_ids)
        ],
        votes=[
            Vote(user_id=PydanticObjectId(), cluster_ids=list(ballot)) for ballot in ballots
        ],
    )


async def _make_decision(client, retro_id, headers, **overrides):
    body = {"text": "Rotate the on-call pager weekly"}
    body.update(overrides)
    resp = await _post_decision(client, retro_id, body, headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_action(client, retro_id, headers, **overrides):
    body = {"description": "Write the runbook"}
    body.update(overrides)
    resp = await _post_action(client, retro_id, body, headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- topic generation --------------------------------------------------------


@pytest.mark.asyncio
async def test_advancing_to_discuss_creates_one_topic_per_cluster(
    client, auth_headers, discussion_retro
):
    """Verified off the database, and against the tally rather than a hardcoded order."""
    retro = discussion_retro["retro"]

    stored = await _stored(retro["id"])
    rows = tally(stored)
    assert len(stored.topics) == len(discussion_retro["clusters"]) == 3

    assert [t.cluster_id for t in stored.topics] == [r.cluster_id for r in rows]
    assert [t.vote_count for t in stored.topics] == [r.vote_count for r in rows]
    assert [t.rank for t in stored.topics] == [r.rank for r in rows] == [1, 2, 3]
    assert [t.vote_count for t in stored.topics] == [3, 1, 0], "Flow, Tooling, Meetings"

    for topic in stored.topics:
        assert topic.status == "pending"
        assert topic.notes == ""
        assert topic.id


@pytest.mark.asyncio
async def test_topics_are_created_when_every_cluster_has_zero_votes(
    client, auth_headers, voting_retro, advance_phase
):
    """No cluster attracted a vote, so the two tie-breaks decide the whole order.

    The timestamps are pinned so that one cluster is plainly oldest and the other
    two share a moment: `created_at` asc settles the first, `id` asc settles the
    rest. A zero-vote cluster still gets a topic — not an error, not an omission,
    because `tally()` keeps them precisely so #9 need not special-case this.
    """
    retro = voting_retro["retro"]
    flow, tooling, meetings = [c["id"] for c in voting_retro["clusters"]]

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pinned = {tooling: base, flow: base + timedelta(minutes=5), meetings: base + timedelta(minutes=5)}
    stored = await _stored(retro["id"])
    for cluster in stored.clusters:
        cluster.created_at = pinned[cluster.id]
    await stored.save()

    advanced = await advance_phase(retro["id"], "discuss")
    topics = advanced["topics"]

    assert len(topics) == 3
    assert [t["vote_count"] for t in topics] == [0, 0, 0]
    assert [t["rank"] for t in topics] == [1, 2, 3]
    assert [t["cluster_id"] for t in topics] == [tooling] + sorted([flow, meetings]), (
        "oldest first, then the tied pair separated by id"
    )
    assert [t.cluster_id for t in (await _stored(retro["id"])).topics] == [
        t["cluster_id"] for t in topics
    ]


@pytest.mark.asyncio
async def test_topics_are_created_when_nobody_voted_at_all(
    client, auth_headers, voting_retro, advance_phase
):
    retro = voting_retro["retro"]
    assert (await _stored(retro["id"])).votes == []

    advanced = await advance_phase(retro["id"], "discuss")
    assert len(advanced["topics"]) == 3
    assert [t["vote_count"] for t in advanced["topics"]] == [0, 0, 0]

    stored = await _stored(retro["id"])
    assert stored.votes == [], "generation does not invent ballots"
    assert len(stored.topics) == 3


@pytest.mark.asyncio
async def test_a_retro_with_no_clusters_advances_with_no_topics(
    client, auth_headers, clustering_retro, advance_phase
):
    """Refusing here would strand the retro — #6 has no way back to an earlier phase."""
    retro = clustering_retro["retro"]
    await advance_phase(retro["id"], "vote")

    advanced = await advance_phase(retro["id"], "discuss")
    assert advanced["clusters"] == []
    assert advanced["topics"] == []
    assert (await _stored(retro["id"])).topics == []


@pytest.mark.asyncio
async def test_decisions_and_actions_work_on_a_retro_with_no_topics(
    client, auth_headers, clustering_retro, advance_phase
):
    """No clusters means no topics, and the discussion still has to be recordable."""
    retro = clustering_retro["retro"]
    await advance_phase(retro["id"], "vote")
    await advance_phase(retro["id"], "discuss")

    decision = await _make_decision(client, retro["id"], auth_headers)
    action = await _make_action(client, retro["id"], auth_headers)
    assert decision["topic_id"] is None
    assert action["topic_id"] is None

    stored = await _stored(retro["id"])
    assert len(stored.decisions) == 1
    assert len(stored.actions) == 1


@pytest.mark.asyncio
async def test_topic_ordering_with_deterministic_cluster_ids():
    """Three clusters, one timestamp, ids inserted in reverse of their sort order.

    `zzz`, `mmm`, `aaa` go in in that order and must come out `aaa`, `mmm`,
    `zzz`. Insertion order is the reverse of id order on purpose: drop the `id`
    key from `tally()`'s sort and Python's stable sort falls back to insertion
    order, which fails this every single time rather than the half the time it
    would with random UUIDs. This is QA's closing note on #8, pinned.
    """
    retro = _build_retro(["zzz", "mmm", "aaa"], ballots=[["zzz", "mmm", "aaa"]])
    assert [c.id for c in retro.clusters] == ["zzz", "mmm", "aaa"]

    topics = create_topics(retro)

    assert [t.cluster_id for t in topics] == ["aaa", "mmm", "zzz"]
    assert [t.rank for t in topics] == [1, 2, 3]
    assert [t.vote_count for t in topics] == [1, 1, 1], "a genuine tie, not an ordering by count"


@pytest.mark.asyncio
async def test_create_topics_is_a_no_op_the_second_time():
    retro = _build_retro(["c1", "c2"], ballots=[["c1"]])

    first = create_topics(retro)
    ids = [t.id for t in first]
    assert len(ids) == 2

    second = create_topics(retro)
    assert [t.id for t in second] == ids, "the same topics, not a regenerated set"
    assert len(retro.topics) == 2


@pytest.mark.asyncio
async def test_generation_counts_votes_through_the_vote_service():
    """One vote-counting loop in the codebase, and it is #8's."""
    from app.services import discussion, votes

    assert discussion.tally is votes.tally


@pytest.mark.asyncio
async def test_advancing_to_discuss_a_second_time_is_refused(
    client, auth_headers, discussion_retro
):
    """Unreachable by design — `next_phase` makes the second call a 400. Asserted."""
    retro = discussion_retro["retro"]
    before = [t.id for t in (await _stored(retro["id"])).topics]

    resp = await client.patch(
        f"/api/retros/{retro['id']}/phase", json={"phase": "discuss"}, headers=auth_headers
    )
    assert resp.status_code == 400, resp.text

    after = await _stored(retro["id"])
    assert [t.id for t in after.topics] == before
    assert after.phase == "discuss"


@pytest.mark.asyncio
async def test_topics_carry_no_name_field():
    """The name is the cluster's, resolved at read time — never stored on a topic."""
    assert "name" not in Topic.model_fields

    retro = _build_retro(["c1"])
    topic = create_topics(retro)[0]
    assert set(topic.model_dump()) == {"id", "cluster_id", "vote_count", "rank", "status", "notes"}


# --- PATCH /api/retros/{id}/topics/{tid} -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("new_status", ["discussed", "skipped", "deferred"])
async def test_facilitator_sets_topic_status(client, auth_headers, discussion_retro, new_status):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    resp = await _patch_topic(
        client, retro["id"], topic["id"], {"status": new_status}, auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == new_status

    stored = await _stored(retro["id"])
    assert stored.topics[0].status == new_status


@pytest.mark.asyncio
async def test_facilitator_sets_and_clears_topic_notes(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    written = await _patch_topic(
        client, retro["id"], topic["id"], {"notes": "we agreed to try WIP limits"}, auth_headers
    )
    assert written.status_code == 200, written.text
    assert written.json()["notes"] == "we agreed to try WIP limits"
    assert (await _stored(retro["id"])).topics[0].notes == "we agreed to try WIP limits"

    cleared = await _patch_topic(client, retro["id"], topic["id"], {"notes": ""}, auth_headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["notes"] == ""
    assert (await _stored(retro["id"])).topics[0].notes == "", "an empty string clears them"


@pytest.mark.asyncio
async def test_topic_status_and_notes_in_one_body(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    resp = await _patch_topic(
        client,
        retro["id"],
        topic["id"],
        {"status": "discussed", "notes": "ship the runbook"},
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = (await _stored(retro["id"])).topics[0]
    assert stored.status == "discussed"
    assert stored.notes == "ship the runbook"


@pytest.mark.asyncio
async def test_an_empty_topic_body_changes_nothing(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]
    await _patch_topic(client, retro["id"], topic["id"], {"notes": "kept"}, auth_headers)

    resp = await _patch_topic(client, retro["id"], topic["id"], {}, auth_headers)
    assert resp.status_code == 200, resp.text

    stored = (await _stored(retro["id"])).topics[0]
    assert stored.status == "pending"
    assert stored.notes == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["done", "Discussed", "", "open", "in_progress"])
async def test_invalid_topic_status_is_rejected(client, auth_headers, discussion_retro, bad):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    resp = await _patch_topic(client, retro["id"], topic["id"], {"status": bad}, auth_headers)
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).topics[0].status == "pending"


@pytest.mark.asyncio
async def test_the_topic_response_carries_its_clusters_name(
    client, auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]
    names = {c["id"]: c["name"] for c in discussion_retro["clusters"]}

    for topic in discussion_retro["topics"]:
        resp = await _patch_topic(client, retro["id"], topic["id"], {}, auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == names[topic["cluster_id"]]


@pytest.mark.asyncio
async def test_the_topic_snapshot_fields_cannot_be_written(
    client, auth_headers, discussion_retro
):
    """`cluster_id`, `vote_count` and `rank` are not in the request schema at all."""
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    resp = await _patch_topic(
        client,
        retro["id"],
        topic["id"],
        {"cluster_id": UNKNOWN_UUID, "vote_count": 99, "rank": 7, "status": "discussed"},
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = (await _stored(retro["id"])).topics[0]
    assert stored.cluster_id == topic["cluster_id"]
    assert stored.vote_count == topic["vote_count"]
    assert stored.rank == topic["rank"]
    assert stored.status == "discussed", "the field that is in the schema still applies"


@pytest.mark.asyncio
async def test_only_the_facilitator_can_patch_a_topic(
    client, second_auth_headers, outsider_auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    member = await _patch_topic(
        client, retro["id"], topic["id"], {"status": "discussed"}, second_auth_headers
    )
    assert member.status_code == 403, member.text

    outsider = await _patch_topic(
        client, retro["id"], topic["id"], {"status": "discussed"}, outsider_auth_headers
    )
    assert outsider.status_code == 403, outsider.text

    assert (await _stored(retro["id"])).topics[0].status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("topic_id", [UNKNOWN_UUID, MALFORMED_ID])
async def test_unknown_and_malformed_topic_ids(client, auth_headers, discussion_retro, topic_id):
    retro = discussion_retro["retro"]

    resp = await _patch_topic(client, retro["id"], topic_id, {"status": "discussed"}, auth_headers)
    assert resp.status_code == 404, resp.text


# --- decisions ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_facilitator_creates_a_decision(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]

    resp = await _post_decision(
        client, retro["id"], {"text": "Cap WIP at three"}, auth_headers
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["text"] == "Cap WIP at three"
    assert data["is_confirmed"] is False
    assert data["topic_id"] is None
    assert data["id"]

    stored = (await _stored(retro["id"])).decisions
    assert len(stored) == 1
    assert stored[0].text == "Cap WIP at three"
    assert stored[0].is_confirmed is False


@pytest.mark.asyncio
async def test_a_decision_can_hang_off_a_topic(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    resp = await _post_decision(
        client, retro["id"], {"topic_id": topic["id"], "text": "Cap WIP"}, auth_headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["topic_id"] == topic["id"]
    assert (await _stored(retro["id"])).decisions[0].topic_id == topic["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"text": "free floating"}, {"topic_id": None, "text": "free floating"}])
async def test_a_decision_without_a_topic_is_accepted(
    client, auth_headers, discussion_retro, body
):
    """Omitted and explicitly null both mean free-floating — #10 needs it."""
    retro = discussion_retro["retro"]

    resp = await _post_decision(client, retro["id"], body, auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["topic_id"] is None


@pytest.mark.asyncio
async def test_a_decision_on_an_unknown_topic_writes_nothing(
    client, auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]

    resp = await _post_decision(
        client, retro["id"], {"topic_id": UNKNOWN_UUID, "text": "Cap WIP"}, auth_headers
    )
    assert resp.status_code == 404, resp.text
    assert (await _stored(retro["id"])).decisions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {}, {"text": None}])
async def test_blank_and_missing_decision_text_is_rejected(
    client, auth_headers, discussion_retro, body
):
    retro = discussion_retro["retro"]

    resp = await _post_decision(client, retro["id"], body, auth_headers)
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).decisions == []


@pytest.mark.asyncio
async def test_decision_text_is_stored_stripped(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]

    resp = await _post_decision(client, retro["id"], {"text": "  Cap WIP  "}, auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["text"] == "Cap WIP"
    assert (await _stored(retro["id"])).decisions[0].text == "Cap WIP"


@pytest.mark.asyncio
async def test_patching_a_decisions_text_and_topic(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]
    decision = await _make_decision(client, retro["id"], auth_headers, topic_id=topic["id"])

    reworded = await _patch_decision(
        client, retro["id"], decision["id"], {"text": "  Cap WIP at two  "}, auth_headers
    )
    assert reworded.status_code == 200, reworded.text
    assert reworded.json()["text"] == "Cap WIP at two"

    detached = await _patch_decision(
        client, retro["id"], decision["id"], {"topic_id": None}, auth_headers
    )
    assert detached.status_code == 200, detached.text
    assert detached.json()["topic_id"] is None

    reattached = await _patch_decision(
        client, retro["id"], decision["id"], {"topic_id": discussion_retro["topics"][1]["id"]},
        auth_headers,
    )
    assert reattached.status_code == 200, reattached.text

    stored = (await _stored(retro["id"])).decisions[0]
    assert stored.text == "Cap WIP at two"
    assert stored.topic_id == discussion_retro["topics"][1]["id"]


@pytest.mark.asyncio
async def test_a_decision_confirms_and_unconfirms(client, auth_headers, discussion_retro):
    """Both directions, and re-confirming is a legal no-op rather than a conflict."""
    retro = discussion_retro["retro"]
    decision = await _make_decision(client, retro["id"], auth_headers)

    confirmed = await _patch_decision(
        client, retro["id"], decision["id"], {"is_confirmed": True}, auth_headers
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["is_confirmed"] is True

    again = await _patch_decision(
        client, retro["id"], decision["id"], {"is_confirmed": True}, auth_headers
    )
    assert again.status_code == 200, again.text

    unconfirmed = await _patch_decision(
        client, retro["id"], decision["id"], {"is_confirmed": False}, auth_headers
    )
    assert unconfirmed.status_code == 200, unconfirmed.text
    assert unconfirmed.json()["is_confirmed"] is False
    assert (await _stored(retro["id"])).decisions[0].is_confirmed is False


@pytest.mark.asyncio
async def test_patching_a_decision_onto_an_unknown_topic_writes_nothing(
    client, auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]
    decision = await _make_decision(client, retro["id"], auth_headers)

    resp = await _patch_decision(
        client, retro["id"], decision["id"], {"topic_id": UNKNOWN_UUID, "text": "moved"},
        auth_headers,
    )
    assert resp.status_code == 404, resp.text

    stored = (await _stored(retro["id"])).decisions[0]
    assert stored.topic_id is None
    assert stored.text == decision["text"], "the text in the rejected body is not written either"


@pytest.mark.asyncio
async def test_deleting_a_decision_twice(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    decision = await _make_decision(client, retro["id"], auth_headers)

    first = await _delete_decision(client, retro["id"], decision["id"], auth_headers)
    assert first.status_code == 204, first.text
    assert first.content == b"", "204 carries no body"
    assert (await _stored(retro["id"])).decisions == []

    second = await _delete_decision(client, retro["id"], decision["id"], auth_headers)
    assert second.status_code == 404, second.text


@pytest.mark.asyncio
async def test_only_the_facilitator_can_touch_decisions(
    client, auth_headers, second_auth_headers, outsider_auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]
    decision = await _make_decision(client, retro["id"], auth_headers)

    for headers in (second_auth_headers, outsider_auth_headers):
        created = await _post_decision(client, retro["id"], {"text": "sneak"}, headers)
        assert created.status_code == 403, created.text

        patched = await _patch_decision(
            client, retro["id"], decision["id"], {"text": "sneak"}, headers
        )
        assert patched.status_code == 403, patched.text

        deleted = await _delete_decision(client, retro["id"], decision["id"], headers)
        assert deleted.status_code == 403, deleted.text

    stored = (await _stored(retro["id"])).decisions
    assert len(stored) == 1
    assert stored[0].text == decision["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_id", [UNKNOWN_UUID, MALFORMED_ID])
async def test_unknown_and_malformed_decision_ids(
    client, auth_headers, discussion_retro, decision_id
):
    retro = discussion_retro["retro"]

    patched = await _patch_decision(client, retro["id"], decision_id, {"text": "x"}, auth_headers)
    assert patched.status_code == 404, patched.text

    deleted = await _delete_decision(client, retro["id"], decision_id, auth_headers)
    assert deleted.status_code == 404, deleted.text


# --- actions -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_facilitator_creates_an_action(
    client, auth_headers, registered_user, discussion_retro
):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    resp = await _post_action(
        client,
        retro["id"],
        {
            "topic_id": topic["id"],
            "description": "  Write the runbook  ",
            "owner_id": registered_user["id"],
        },
        auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["description"] == "Write the runbook", "stripped, as decision text is"
    assert data["status"] == "open"
    assert data["owner_id"] == registered_user["id"]
    assert data["due_date"] is None
    assert data["topic_id"] == topic["id"]
    assert data["owner_name"] is None, (
        "#10 adds owner_name to every action response; #9 never writes it, so it is "
        "present and null rather than absent"
    )

    stored = (await _stored(retro["id"])).actions[0]
    assert stored.owner_name is None, "and never written"
    assert str(stored.owner_id) == registered_user["id"]
    assert stored.status == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{"description": "unowned"}, {"description": "unowned", "owner_id": None}]
)
async def test_an_action_without_an_owner_is_accepted(
    client, auth_headers, discussion_retro, body
):
    """#10 needs somewhere to put an action whose owner it could not match."""
    retro = discussion_retro["retro"]

    resp = await _post_action(client, retro["id"], body, auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["owner_id"] is None
    assert (await _stored(retro["id"])).actions[0].owner_id is None


@pytest.mark.asyncio
async def test_an_owner_who_is_not_a_project_member_writes_nothing(
    client, auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]

    resp = await _post_action(
        client, retro["id"], {"description": "Write it", "owner_id": UNKNOWN_ID}, auth_headers
    )
    assert resp.status_code == 404, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
async def test_a_malformed_owner_id_is_a_body_shape_error(
    client, auth_headers, discussion_retro
):
    """Pydantic parses `owner_id`, so `abc` never reaches the membership lookup."""
    retro = discussion_retro["retro"]

    resp = await _post_action(
        client, retro["id"], {"description": "Write it", "owner_id": MALFORMED_ID}, auth_headers
    )
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
async def test_a_due_date_in_the_past_is_accepted(client, auth_headers, discussion_retro):
    """A retro routinely records an already-late commitment."""
    retro = discussion_retro["retro"]
    past = datetime(2020, 3, 1, 12, 0, tzinfo=timezone.utc)

    resp = await _post_action(
        client, retro["id"], {"description": "Overdue", "due_date": past.isoformat()}, auth_headers
    )
    assert resp.status_code == 201, resp.text
    assert (await _stored(retro["id"])).actions[0].due_date.replace(tzinfo=timezone.utc) == past


@pytest.mark.asyncio
async def test_a_due_date_can_be_omitted_set_and_cleared(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    later = datetime(2030, 6, 1, 9, 0, tzinfo=timezone.utc)

    omitted = await _make_action(client, retro["id"], auth_headers)
    assert omitted["due_date"] is None

    explicit_null = await _make_action(client, retro["id"], auth_headers, due_date=None)
    assert explicit_null["due_date"] is None

    dated = await _patch_action(
        client, retro["id"], omitted["id"], {"due_date": later.isoformat()}, auth_headers
    )
    assert dated.status_code == 200, dated.text
    assert dated.json()["due_date"] is not None

    cleared = await _patch_action(
        client, retro["id"], omitted["id"], {"due_date": None}, auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["due_date"] is None
    assert (await _stored(retro["id"])).actions[0].due_date is None


@pytest.mark.asyncio
async def test_an_unparseable_due_date_is_rejected(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]

    resp = await _post_action(
        client, retro["id"], {"description": "Write it", "due_date": "next tuesday"}, auth_headers
    )
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"description": ""}, {"description": "   "}, {}])
async def test_blank_and_missing_action_descriptions_are_rejected(
    client, auth_headers, discussion_retro, body
):
    retro = discussion_retro["retro"]

    resp = await _post_action(client, retro["id"], body, auth_headers)
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["closed", "Open", "", "done!"])
async def test_an_action_status_outside_open_and_done_is_rejected(
    client, auth_headers, discussion_retro, bad
):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers)

    resp = await _patch_action(client, retro["id"], action["id"], {"status": bad}, auth_headers)
    assert resp.status_code == 422, resp.text
    assert (await _stored(retro["id"])).actions[0].status == "open"


@pytest.mark.asyncio
async def test_an_action_on_an_unknown_topic_writes_nothing(
    client, auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]

    created = await _post_action(
        client, retro["id"], {"description": "Write it", "topic_id": UNKNOWN_UUID}, auth_headers
    )
    assert created.status_code == 404, created.text
    assert (await _stored(retro["id"])).actions == []

    action = await _make_action(client, retro["id"], auth_headers)
    moved = await _patch_action(
        client, retro["id"], action["id"],
        {"topic_id": UNKNOWN_UUID, "description": "moved"}, auth_headers,
    )
    assert moved.status_code == 404, moved.text

    stored = (await _stored(retro["id"])).actions[0]
    assert stored.topic_id is None
    assert stored.description == action["description"]


# --- the action permission table ---------------------------------------------


@pytest.mark.asyncio
async def test_facilitator_may_change_anything_on_an_action(
    client, auth_headers, second_user, discussion_retro
):
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]
    action = await _make_action(client, retro["id"], auth_headers)

    resp = await _patch_action(
        client,
        retro["id"],
        action["id"],
        {
            "description": "Rewrite the runbook",
            "owner_id": second_user["id"],
            "topic_id": topic["id"],
            "status": "done",
            "due_date": "2030-01-01T00:00:00Z",
        },
        auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = (await _stored(retro["id"])).actions[0]
    assert stored.description == "Rewrite the runbook"
    assert str(stored.owner_id) == second_user["id"]
    assert stored.topic_id == topic["id"]
    assert stored.status == "done"
    assert stored.due_date is not None


@pytest.mark.asyncio
async def test_a_facilitator_who_is_also_the_owner_keeps_full_rights(
    client, auth_headers, registered_user, discussion_retro
):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=registered_user["id"])

    resp = await _patch_action(
        client, retro["id"], action["id"], {"description": "Still mine to rewrite"}, auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert (await _stored(retro["id"])).actions[0].description == "Still mine to rewrite"


@pytest.mark.asyncio
async def test_an_owner_may_set_status_and_due_date(
    client, auth_headers, second_auth_headers, second_user, discussion_retro
):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=second_user["id"])

    resp = await _patch_action(
        client,
        retro["id"],
        action["id"],
        {"status": "done", "due_date": "2030-01-01T00:00:00Z"},
        second_auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = (await _stored(retro["id"])).actions[0]
    assert stored.status == "done"
    assert stored.due_date is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["description", "owner_id", "topic_id"])
async def test_an_owner_sending_a_restricted_field_writes_nothing_at_all(
    client, auth_headers, second_auth_headers, second_user, registered_user,
    discussion_retro, restricted,
):
    """403, and the `status` in the same body does not survive it. All or nothing."""
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=second_user["id"])
    values = {
        "description": "Rewritten by the owner",
        "owner_id": registered_user["id"],
        "topic_id": discussion_retro["topics"][0]["id"],
    }

    resp = await _patch_action(
        client,
        retro["id"],
        action["id"],
        {"status": "done", restricted: values[restricted]},
        second_auth_headers,
    )
    assert resp.status_code == 403, resp.text

    stored = (await _stored(retro["id"])).actions[0]
    assert stored.status == "open", "the permitted field in the rejected body is not written"
    assert stored.description == action["description"]
    assert str(stored.owner_id) == second_user["id"]
    assert stored.topic_id is None


@pytest.mark.asyncio
async def test_a_member_who_is_not_the_owner_is_refused(
    client, auth_headers, second_auth_headers, registered_user, discussion_retro
):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=registered_user["id"])

    resp = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, second_auth_headers
    )
    assert resp.status_code == 403, resp.text
    assert (await _stored(retro["id"])).actions[0].status == "open"


@pytest.mark.asyncio
async def test_the_owner_of_a_different_action_is_refused(
    client, auth_headers, second_auth_headers, second_user, registered_user, discussion_retro
):
    """Owning one action buys nothing on the next one."""
    retro = discussion_retro["retro"]
    mine = await _make_action(
        client, retro["id"], auth_headers, owner_id=second_user["id"], description="Bob's"
    )
    theirs = await _make_action(
        client, retro["id"], auth_headers, owner_id=registered_user["id"], description="Alice's"
    )

    resp = await _patch_action(
        client, retro["id"], theirs["id"], {"status": "done"}, second_auth_headers
    )
    assert resp.status_code == 403, resp.text

    stored = {a.id: a for a in (await _stored(retro["id"])).actions}
    assert stored[theirs["id"]].status == "open"
    assert stored[mine["id"]].status == "open"


@pytest.mark.asyncio
async def test_an_unassigned_action_is_the_facilitators_alone(
    client, auth_headers, second_auth_headers, outsider_auth_headers, discussion_retro
):
    """`owner_id: null` means no owner, so no member can claim owner rights on it."""
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers)
    assert action["owner_id"] is None

    member = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, second_auth_headers
    )
    assert member.status_code == 403, member.text

    outsider = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, outsider_auth_headers
    )
    assert outsider.status_code == 403, outsider.text
    assert (await _stored(retro["id"])).actions[0].status == "open"

    facilitator = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, auth_headers
    )
    assert facilitator.status_code == 200, facilitator.text


@pytest.mark.asyncio
async def test_a_non_member_cannot_patch_an_action(
    client, auth_headers, outsider_auth_headers, second_user, discussion_retro
):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=second_user["id"])

    resp = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, outsider_auth_headers
    )
    assert resp.status_code == 403, resp.text
    assert (await _stored(retro["id"])).actions[0].status == "open"


@pytest.mark.asyncio
async def test_the_facilitator_reassigns_an_action(
    client, auth_headers, registered_user, second_user, discussion_retro
):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=registered_user["id"])

    reassigned = await _patch_action(
        client, retro["id"], action["id"], {"owner_id": second_user["id"]}, auth_headers
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["owner_id"] == second_user["id"]

    unassigned = await _patch_action(
        client, retro["id"], action["id"], {"owner_id": None}, auth_headers
    )
    assert unassigned.status_code == 200, unassigned.text
    assert unassigned.json()["owner_id"] is None
    assert (await _stored(retro["id"])).actions[0].owner_id is None

    stranger = await _patch_action(
        client, retro["id"], action["id"], {"owner_id": UNKNOWN_ID}, auth_headers
    )
    assert stranger.status_code == 404, stranger.text
    assert (await _stored(retro["id"])).actions[0].owner_id is None


@pytest.mark.asyncio
async def test_an_owner_removed_from_the_project_keeps_the_action(
    client, auth_headers, second_auth_headers, second_user, discussion_retro
):
    """History is not rewritten — the same principle as #8's `members_voted`."""
    retro = discussion_retro["retro"]
    project_id = discussion_retro["cycle"]["project_id"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=second_user["id"])

    removed = await client.delete(
        f"/api/projects/{project_id}/members/{second_user['id']}", headers=auth_headers
    )
    assert removed.status_code == 200, removed.text

    stored = (await _stored(retro["id"])).actions[0]
    assert str(stored.owner_id) == second_user["id"], "not deleted, not reassigned"

    refused = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, second_auth_headers
    )
    assert refused.status_code == 403, "get_retro_for_member refuses before any owner check"

    back_to_them = await _patch_action(
        client, retro["id"], action["id"], {"owner_id": second_user["id"]}, auth_headers
    )
    assert back_to_them.status_code == 404, "no longer a current member"

    reassigned = await _patch_action(
        client, retro["id"], action["id"], {"owner_id": None, "status": "done"}, auth_headers
    )
    assert reassigned.status_code == 200, reassigned.text
    assert (await _stored(retro["id"])).actions[0].owner_id is None


@pytest.mark.asyncio
async def test_deleting_an_action_twice(client, auth_headers, discussion_retro):
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers)

    first = await _delete_action(client, retro["id"], action["id"], auth_headers)
    assert first.status_code == 204, first.text
    assert first.content == b""
    assert (await _stored(retro["id"])).actions == []

    second = await _delete_action(client, retro["id"], action["id"], auth_headers)
    assert second.status_code == 404, second.text


@pytest.mark.asyncio
async def test_only_the_facilitator_can_delete_an_action(
    client, auth_headers, second_auth_headers, outsider_auth_headers, second_user,
    discussion_retro,
):
    """Not even the owner — an owner cannot delete their way out of a commitment."""
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=second_user["id"])

    owner = await _delete_action(client, retro["id"], action["id"], second_auth_headers)
    assert owner.status_code == 403, owner.text

    outsider = await _delete_action(client, retro["id"], action["id"], outsider_auth_headers)
    assert outsider.status_code == 403, outsider.text

    assert len((await _stored(retro["id"])).actions) == 1


@pytest.mark.asyncio
async def test_a_non_member_cannot_create_an_action(
    client, outsider_auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]

    resp = await _post_action(client, retro["id"], {"description": "sneak"}, outsider_auth_headers)
    assert resp.status_code == 403, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
async def test_a_member_who_is_not_the_facilitator_cannot_create_an_action(
    client, second_auth_headers, discussion_retro
):
    retro = discussion_retro["retro"]

    resp = await _post_action(client, retro["id"], {"description": "sneak"}, second_auth_headers)
    assert resp.status_code == 403, resp.text
    assert (await _stored(retro["id"])).actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action_id", [UNKNOWN_UUID, MALFORMED_ID])
async def test_unknown_and_malformed_action_ids(
    client, auth_headers, discussion_retro, action_id
):
    retro = discussion_retro["retro"]

    patched = await _patch_action(client, retro["id"], action_id, {"status": "done"}, auth_headers)
    assert patched.status_code == 404, patched.text

    deleted = await _delete_action(client, retro["id"], action_id, auth_headers)
    assert deleted.status_code == 404, deleted.text


# --- cross-cutting -----------------------------------------------------------


@pytest.mark.asyncio
async def test_every_endpoint_is_refused_in_the_vote_phase(
    client, auth_headers, voting_retro
):
    """Before `discuss`: the ids do not exist yet, and the phase is checked first."""
    retro = voting_retro["retro"]

    assert (
        await _patch_topic(client, retro["id"], UNKNOWN_UUID, {"status": "discussed"}, auth_headers)
    ).status_code == 400
    assert (
        await _post_decision(client, retro["id"], {"text": "too early"}, auth_headers)
    ).status_code == 400
    assert (
        await _patch_decision(client, retro["id"], UNKNOWN_UUID, {"text": "x"}, auth_headers)
    ).status_code == 400
    assert (
        await _delete_decision(client, retro["id"], UNKNOWN_UUID, auth_headers)
    ).status_code == 400
    assert (
        await _post_action(client, retro["id"], {"description": "too early"}, auth_headers)
    ).status_code == 400
    assert (
        await _patch_action(client, retro["id"], UNKNOWN_UUID, {"status": "done"}, auth_headers)
    ).status_code == 400
    assert (
        await _delete_action(client, retro["id"], UNKNOWN_UUID, auth_headers)
    ).status_code == 400

    stored = await _stored(retro["id"])
    assert stored.decisions == [] and stored.actions == []


@pytest.mark.asyncio
async def test_every_endpoint_is_refused_once_the_retro_is_done(
    client, auth_headers, discussion_retro, advance_phase
):
    """After #11 publishes, the whole discussion is frozen."""
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]
    decision = await _make_decision(client, retro["id"], auth_headers)
    action = await _make_action(client, retro["id"], auth_headers)

    await advance_phase(retro["id"], "done")

    assert (
        await _patch_topic(client, retro["id"], topic["id"], {"status": "discussed"}, auth_headers)
    ).status_code == 400
    assert (
        await _post_decision(client, retro["id"], {"text": "too late"}, auth_headers)
    ).status_code == 400
    assert (
        await _patch_decision(client, retro["id"], decision["id"], {"text": "x"}, auth_headers)
    ).status_code == 400
    assert (
        await _delete_decision(client, retro["id"], decision["id"], auth_headers)
    ).status_code == 400
    assert (
        await _post_action(client, retro["id"], {"description": "too late"}, auth_headers)
    ).status_code == 400
    assert (
        await _patch_action(client, retro["id"], action["id"], {"status": "done"}, auth_headers)
    ).status_code == 400
    assert (
        await _delete_action(client, retro["id"], action["id"], auth_headers)
    ).status_code == 400

    stored = await _stored(retro["id"])
    assert stored.topics[0].status == "pending"
    assert len(stored.decisions) == 1 and len(stored.actions) == 1


@pytest.mark.asyncio
async def test_check_order_on_the_facilitator_only_endpoints(
    client, second_auth_headers, voting_retro
):
    """Membership+facilitator → phase → id lookup, so 403 beats the wrong phase."""
    retro = voting_retro["retro"]

    assert (
        await _patch_topic(
            client, retro["id"], UNKNOWN_UUID, {"status": "discussed"}, second_auth_headers
        )
    ).status_code == 403, "a non-facilitator member in the vote phase, not 400"
    assert (
        await _post_decision(client, retro["id"], {"text": "x"}, second_auth_headers)
    ).status_code == 403
    assert (
        await _delete_action(client, retro["id"], UNKNOWN_UUID, second_auth_headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_check_order_on_the_action_patch(
    client, auth_headers, second_auth_headers, registered_user, discussion_retro, advance_phase
):
    """Membership → phase → action lookup → role, so the wrong phase beats 403.

    The mirror image of the facilitator-only order above, and forced: nobody can
    know who owns an action until the action has been loaded, and the action
    cannot be loaded before the phase gate has let the request through.
    """
    retro = discussion_retro["retro"]
    action = await _make_action(client, retro["id"], auth_headers, owner_id=registered_user["id"])

    in_phase = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, second_auth_headers
    )
    assert in_phase.status_code == 403, "in the discuss phase, a non-owner member is 403"

    await advance_phase(retro["id"], "done")

    wrong_phase = await _patch_action(
        client, retro["id"], action["id"], {"status": "done"}, second_auth_headers
    )
    assert wrong_phase.status_code == 400, "out of phase, the same caller is 400, not 403"


@pytest.mark.asyncio
async def test_unknown_and_malformed_retro_ids_on_every_endpoint(
    client, auth_headers, discussion_retro
):
    retro_ids = [UNKNOWN_ID, MALFORMED_ID]
    for retro_id in retro_ids:
        responses = [
            await _patch_topic(client, retro_id, UNKNOWN_UUID, {"status": "discussed"}, auth_headers),
            await _post_decision(client, retro_id, {"text": "x"}, auth_headers),
            await _patch_decision(client, retro_id, UNKNOWN_UUID, {"text": "x"}, auth_headers),
            await _delete_decision(client, retro_id, UNKNOWN_UUID, auth_headers),
            await _post_action(client, retro_id, {"description": "x"}, auth_headers),
            await _patch_action(client, retro_id, UNKNOWN_UUID, {"status": "done"}, auth_headers),
            await _delete_action(client, retro_id, UNKNOWN_UUID, auth_headers),
        ]
        for resp in responses:
            assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_every_endpoint_requires_authentication(client, discussion_retro):
    """HTTPBearer rejects before the dependency runs, so this is 401 and not 403."""
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]

    responses = [
        await _patch_topic(client, retro["id"], topic["id"], {"status": "discussed"}),
        await _post_decision(client, retro["id"], {"text": "x"}),
        await _patch_decision(client, retro["id"], UNKNOWN_UUID, {"text": "x"}),
        await _delete_decision(client, retro["id"], UNKNOWN_UUID),
        await _post_action(client, retro["id"], {"description": "x"}),
        await _patch_action(client, retro["id"], UNKNOWN_UUID, {"status": "done"}),
        await _delete_action(client, retro["id"], UNKNOWN_UUID),
    ]
    for resp in responses:
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_no_discussion_endpoint_expresses_a_conflict():
    """Double generation is unreachable and re-confirming is a no-op — nothing conflicts."""
    import app.api.discussion as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "409" not in source
    assert "CONFLICT" not in source


@pytest.mark.asyncio
async def test_the_retro_payload_carries_every_mutation(
    client, auth_headers, second_user, registered_user, discussion_retro
):
    """The one read path, and the one #11 builds on."""
    retro = discussion_retro["retro"]
    topic = discussion_retro["topics"][0]
    names = {c["id"]: c["name"] for c in discussion_retro["clusters"]}

    await _patch_topic(
        client, retro["id"], topic["id"], {"status": "discussed", "notes": "WIP limits"},
        auth_headers,
    )
    decision = await _make_decision(client, retro["id"], auth_headers, topic_id=topic["id"])
    await _patch_decision(client, retro["id"], decision["id"], {"is_confirmed": True}, auth_headers)
    await _make_action(
        client, retro["id"], auth_headers,
        owner_id=second_user["id"], due_date="2030-01-01T00:00:00Z",
    )

    resp = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    topics = body["topics"]
    assert len(topics) == 3
    for entry in topics:
        assert entry["name"] == names[entry["cluster_id"]], "resolved, not stored"
        assert set(entry) == {"id", "cluster_id", "name", "vote_count", "rank", "status", "notes"}
    assert topics[0]["status"] == "discussed"
    assert topics[0]["notes"] == "WIP limits"
    assert [t["rank"] for t in topics] == [1, 2, 3]

    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["is_confirmed"] is True
    assert body["decisions"][0]["topic_id"] == topic["id"]

    assert len(body["actions"]) == 1
    assert body["actions"][0]["owner_id"] == second_user["id"]
    assert body["actions"][0]["due_date"] is not None
    assert body["actions"][0]["status"] == "open"


@pytest.mark.asyncio
async def test_the_retro_payload_still_hides_who_voted_for_what(
    client, auth_headers, discussion_retro
):
    """#8's vote secrecy is not weakened by anything here."""
    retro = discussion_retro["retro"]

    resp = await client.get(f"/api/retros/{retro['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    votes = resp.json()["votes"]

    assert len(votes) == 2
    for vote in votes:
        assert set(vote) == {"user_id", "submitted_at"}
    assert "cluster_ids" not in json.dumps(votes)
