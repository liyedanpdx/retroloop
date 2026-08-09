from datetime import datetime, timezone

import pytest
from beanie import PydanticObjectId

from app.models.cycle import CLOSED, RETRO, Cycle
from app.models.feedback import FeedbackCard
from app.models.project import Project
from app.models.retro import Action, Decision, Retrospective, Vote
from app.models.user import User

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"


async def _get(client, retro_id, headers=None):
    kwargs = {} if headers is None else {"headers": headers}
    return await client.get(f"/api/retros/{retro_id}/summary", **kwargs)


async def _publish(client, retro_id, headers=None):
    kwargs = {} if headers is None else {"headers": headers}
    return await client.post(f"/api/retros/{retro_id}/summary/publish", **kwargs)


@pytest.mark.asyncio
async def test_summary_full_shape_order_resolution_and_participation(
    client, auth_headers, second_user, discussion_retro
):
    retro = await Retrospective.get(discussion_retro["retro"]["id"])
    project = await Project.get(discussion_retro["cycle"]["project_id"])
    alice = await User.find_one(User.email == "alice@example.com")
    bob = await User.find_one(User.email == "bob@example.com")
    topic = retro.topics[1]
    retro.topics = list(reversed(retro.topics))
    retro.decisions = [
        Decision(id="d1", topic_id=topic.id, text="Keep it", is_confirmed=True),
        Decision(id="d2", topic_id=None, text="Draft", is_confirmed=False),
        Decision(id="d3", topic_id=None, text="Ship it", is_confirmed=True),
    ]
    due = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    retro.actions = [
        Action(id="a1", topic_id=topic.id, description="Owned", owner_id=bob.id, owner_name="Wrong", due_date=due),
        Action(id="a2", description="Named", owner_name="  Dana Wu  "),
        Action(id="a3", description="Free", owner_name="   "),
    ]
    # A departed user's old ballot must not count.
    retro.votes.append(Vote(user_id=PydanticObjectId(), cluster_ids=[]))
    await retro.save()

    # Repeated authored cards count Alice once; anonymous cards cannot count.
    await FeedbackCard(
        cycle_id=retro.cycle_id, author_id=bob.id, category="continue", text="Bob card"
    ).insert()
    await FeedbackCard(
        cycle_id=retro.cycle_id, author_id=None, category="stop", text="Secret", is_anonymous=True
    ).insert()
    other_cycle = await Cycle(project_id=project.id, created_by=alice.id).insert()
    await FeedbackCard(
        cycle_id=other_cycle.id, author_id=alice.id, category="start", text="Other cycle"
    ).insert()

    response = await _get(client, str(retro.id), auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"topics", "decisions", "actions", "participation", "feedback_cards"}
    assert [row["rank"] for row in body["topics"]] == [1, 2, 3]
    assert set(body["topics"][0]) == {"id", "cluster_id", "name", "vote_count", "rank", "status", "notes"}
    assert body["topics"][2]["vote_count"] == 0
    assert body["topics"][0]["notes"] == ""
    assert [row["id"] for row in body["decisions"]] == ["d1", "d3"]
    assert body["decisions"] == [
        {"id": "d1", "topic_id": topic.id, "topic": next(c.name for c in retro.clusters if c.id == topic.cluster_id), "text": "Keep it"},
        {"id": "d3", "topic_id": None, "topic": None, "text": "Ship it"},
    ]
    assert [row["id"] for row in body["actions"]] == ["a1", "a2", "a3"]
    assert body["actions"][0]["owner"] == "Bob"
    assert body["actions"][0]["owner_id"] == str(bob.id)
    assert body["actions"][0]["topic"] is not None
    assert body["actions"][0]["due_date"].startswith("2026-09-01T12:00:00")
    assert body["actions"][1]["owner"] == "Dana Wu"
    assert body["actions"][2]["owner"] is None
    assert body["participation"] == {"total_members": 2, "submitted_feedback": 2, "voted": 2}
    assert "Other cycle" not in [row["text"] for row in body["feedback_cards"]]
    assert [row["created_at"] for row in body["feedback_cards"]] == sorted(row["created_at"] for row in body["feedback_cards"])
    anonymous = next(row for row in body["feedback_cards"] if row["is_anonymous"])
    assert anonymous["author_id"] is None
    assert anonymous["text"] == "Secret"


@pytest.mark.asyncio
async def test_preview_permissions_and_missing_ids(
    client, auth_headers, second_auth_headers, outsider_auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    assert (await _get(client, retro_id, auth_headers)).status_code == 200
    assert (await _get(client, retro_id, second_auth_headers)).status_code == 403
    assert (await _get(client, retro_id, outsider_auth_headers)).status_code == 403
    assert (await _get(client, retro_id)).status_code == 401
    assert (await _get(client, UNKNOWN_ID, auth_headers)).status_code == 404
    assert (await _get(client, MALFORMED_ID, auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_publish_closes_both_documents_and_is_one_way(
    client, auth_headers, second_auth_headers, outsider_auth_headers, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    cycle_id = discussion_retro["cycle"]["id"]
    preview = (await _get(client, retro_id, auth_headers)).json()

    assert (await _publish(client, retro_id, second_auth_headers)).status_code == 403
    assert (await _publish(client, retro_id, outsider_auth_headers)).status_code == 403
    assert (await _publish(client, retro_id)).status_code == 401
    assert (await _publish(client, UNKNOWN_ID, auth_headers)).status_code == 404
    assert (await _publish(client, MALFORMED_ID, auth_headers)).status_code == 404

    published = await _publish(client, retro_id, auth_headers)
    assert published.status_code == 200, published.text
    assert published.json() == preview
    stored_retro = await Retrospective.get(retro_id)
    stored_cycle = await Cycle.get(cycle_id)
    assert stored_retro.phase == "done"
    assert stored_cycle.status == CLOSED
    assert stored_cycle.closed_at is not None
    # Motor's default codec reads BSON UTC datetimes back as naive values; the
    # stored instant must nevertheless be current UTC, not local wall time.
    closed_at_utc = stored_cycle.closed_at.replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - closed_at_utc).total_seconds()) < 10

    assert (await _get(client, retro_id, second_auth_headers)).status_code == 200
    again = await _publish(client, retro_id, auth_headers)
    assert again.status_code == 400
    assert (await Retrospective.get(retro_id)).phase == "done"
    assert (await Cycle.get(cycle_id)).status == CLOSED


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["reveal", "cluster", "vote", "done"])
async def test_publish_rejects_every_invalid_phase_without_changes(
    client, auth_headers, discussion_retro, phase
):
    retro = await Retrospective.get(discussion_retro["retro"]["id"])
    cycle = await Cycle.get(retro.cycle_id)
    retro.phase = phase
    await retro.save()
    before_status, before_closed_at = cycle.status, cycle.closed_at

    response = await _publish(client, str(retro.id), auth_headers)
    assert response.status_code == 400
    assert (await Retrospective.get(retro.id)).phase == phase
    stored_cycle = await Cycle.get(cycle.id)
    assert (stored_cycle.status, stored_cycle.closed_at) == (before_status, before_closed_at)


@pytest.mark.asyncio
async def test_empty_summary_publishes_and_get_is_assembled_on_each_read(
    client, auth_headers, project, cycle, reveal, advance_phase
):
    retro = await reveal(cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)

    first = await _get(client, retro["id"], auth_headers)
    assert first.status_code == 200
    assert first.json() == {
        "topics": [], "decisions": [], "actions": [],
        "participation": {"total_members": 1, "submitted_feedback": 0, "voted": 0},
        "feedback_cards": [],
    }

    stored = await Retrospective.get(retro["id"])
    stored.decisions.append(Decision(id="late", text="Latest", is_confirmed=True))
    await stored.save()
    second = await _get(client, retro["id"], auth_headers)
    assert [row["text"] for row in second.json()["decisions"]] == ["Latest"]

    published = await _publish(client, retro["id"], auth_headers)
    assert published.status_code == 200
    assert [row["text"] for row in published.json()["decisions"]] == ["Latest"]
    # Reading done state does not write a snapshot or mutate source data.
    before = (await Retrospective.get(retro["id"])).model_dump()
    assert (await _get(client, retro["id"], auth_headers)).status_code == 200
    assert (await Retrospective.get(retro["id"])).model_dump() == before
