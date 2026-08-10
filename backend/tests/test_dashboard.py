"""GET /api/projects/{id}/dashboard — one read for #14's project page (#31).

The privacy assertions are the ones to keep if this file is ever trimmed. A
dashboard that leaks who has not submitted yet, or what a card said, undoes #5;
one that leaks a ballot undoes #8. Those are checked against the whole response
text rather than field by field, so a future field cannot smuggle them back in.
"""

import pytest
from beanie import PydanticObjectId

from app.models.cycle import CLOSED, Cycle
from app.models.feedback import FeedbackCard
from app.models.retro import Action, Retrospective
from app.models.user import User

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"


def _url(project_id: str) -> str:
    return f"/api/projects/{project_id}/dashboard"


# --- access ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_any_current_member_may_read_it(
    client, auth_headers, second_auth_headers, project_with_member
):
    for headers in (auth_headers, second_auth_headers):
        response = await client.get(_url(project_with_member["id"]), headers=headers)
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_outsiders_unknown_ids_and_anonymous_callers_are_refused(
    client, auth_headers, outsider_auth_headers, project
):
    assert (
        await client.get(_url(project["id"]), headers=outsider_auth_headers)
    ).status_code == 403
    assert (await client.get(_url(project["id"]))).status_code == 401
    assert (await client.get(_url(UNKNOWN_ID), headers=auth_headers)).status_code == 404
    assert (await client.get(_url(MALFORMED_ID), headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_one_projects_dashboard_shows_nothing_of_another(
    client, auth_headers, project, cycle, add_card
):
    await add_card(cycle["id"], text="alpha only")
    other = await client.post(
        "/api/projects", json={"name": "Team Beta", "description": None}, headers=auth_headers
    )

    response = await client.get(_url(other.json()["id"]), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["current_cycle"] is None
    assert body["past_retros"] == []
    assert body["open_actions"] == []
    assert cycle["id"] not in response.text


# --- shape -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_members_carry_the_identity_the_ui_needs(
    client, auth_headers, project_with_member
):
    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    members = response.json()["members"]

    assert [m["display_name"] for m in members] == ["Alice", "Bob"], "membership order"
    assert set(members[0]) == {"user_id", "display_name", "email", "role", "joined_at"}
    assert members[0]["role"] == "facilitator"
    assert members[1]["role"] == "member"
    assert members[1]["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_an_empty_project_is_all_nulls_and_empty_arrays(client, auth_headers, project):
    response = await client.get(_url(project["id"]), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"members", "current_cycle", "past_retros", "open_actions"}
    assert body["current_cycle"] is None
    assert body["past_retros"] == []
    assert body["open_actions"] == []
    assert len(body["members"]) == 1


@pytest.mark.asyncio
async def test_a_collecting_cycle_with_no_retro_yet(
    client, auth_headers, project_with_member, shared_cycle, add_card
):
    await add_card(shared_cycle["id"])
    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)

    current = response.json()["current_cycle"]
    assert set(current) == {"id", "status", "created_at", "closed_at", "progress", "retro"}
    assert current["id"] == shared_cycle["id"]
    assert current["status"] == "collecting"
    assert current["closed_at"] is None
    assert current["retro"] is None, "no retrospective has been started"
    assert current["progress"] == {"submitted_members": 1, "total_members": 2}


@pytest.mark.asyncio
async def test_an_active_retro_is_named_with_its_phase(
    client, auth_headers, project_with_member, clustering_retro
):
    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    current = response.json()["current_cycle"]

    assert current["status"] == "retro"
    assert current["retro"] == {"id": clustering_retro["retro"]["id"], "phase": "cluster"}


# --- progress ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_member_counts_once_however_many_cards_they_wrote(
    client, auth_headers, project_with_member, shared_cycle, add_card
):
    for text in ("one", "two", "three"):
        await add_card(shared_cycle["id"], text=text)

    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert response.json()["current_cycle"]["progress"] == {
        "submitted_members": 1,
        "total_members": 2,
    }


@pytest.mark.asyncio
async def test_an_anonymous_only_submitter_is_counted(
    client, auth_headers, second_auth_headers, project_with_member, shared_cycle, add_card
):
    """#28 fixed what #31 had to leave undone.

    This asserted 0 when it was written: #5 erased the author, so there was
    nothing on the card to count. #28 put the marker on the cycle instead, and
    the count is now exact.
    """
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True)

    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert response.json()["current_cycle"]["progress"]["submitted_members"] == 1


@pytest.mark.asyncio
async def test_a_removed_members_cards_stop_counting(
    client, auth_headers, second_auth_headers, project_with_member, shared_cycle, add_card,
    second_user,
):
    await add_card(shared_cycle["id"], headers=second_auth_headers)
    before = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert before.json()["current_cycle"]["progress"] == {
        "submitted_members": 1,
        "total_members": 2,
    }

    removed = await client.delete(
        f"/api/projects/{project_with_member['id']}/members/{second_user['id']}",
        headers=auth_headers,
    )
    assert removed.status_code == 200, removed.text

    after = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert after.json()["current_cycle"]["progress"] == {
        "submitted_members": 0,
        "total_members": 1,
    }, "a count that could exceed total_members would be worse than useless"


# --- past retros -------------------------------------------------------------


@pytest.mark.asyncio
async def test_past_retros_are_newest_first_and_exclude_the_current_one(
    client, auth_headers, project, cycle, reveal, advance_phase
):
    finished = []
    for _ in range(2):
        retro = await reveal(cycle["id"])
        for phase in ("cluster", "vote", "discuss"):
            retro = await advance_phase(retro["id"], phase)
        published = await client.post(
            f"/api/retros/{retro['id']}/summary/publish", headers=auth_headers
        )
        assert published.status_code == 200, published.text
        finished.append(retro["id"])
        opened = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
        cycle = opened.json()

    live = await reveal(cycle["id"])
    response = await client.get(_url(project["id"]), headers=auth_headers)
    body = response.json()

    assert [row["id"] for row in body["past_retros"]] == list(reversed(finished))
    assert set(body["past_retros"][0]) == {"id", "cycle_id", "phase", "created_at", "closed_at"}
    assert body["past_retros"][0]["phase"] == "done"
    assert body["past_retros"][0]["closed_at"] is not None
    assert body["current_cycle"]["retro"]["id"] == live["id"]
    assert live["id"] not in [row["id"] for row in body["past_retros"]]


# --- open actions ------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_actions_are_listed_with_owners_and_closed_ones_are_not(
    client, auth_headers, second_user, project_with_member, discussion_retro
):
    retro_id = discussion_retro["retro"]["id"]
    bob = await User.find_one(User.email == "bob@example.com")

    assigned = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Pair on the flaky test", "owner_id": str(bob.id)},
        headers=auth_headers,
    )
    unassigned = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Nobody yet"},
        headers=auth_headers,
    )
    done = await client.post(
        f"/api/retros/{retro_id}/actions", json={"description": "Already did"}, headers=auth_headers
    )
    closed = await client.patch(
        f"/api/retros/{retro_id}/actions/{done.json()['id']}",
        json={"status": "done"},
        headers=auth_headers,
    )
    assert closed.status_code == 200, closed.text

    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    actions = response.json()["open_actions"]

    assert [a["id"] for a in actions] == [assigned.json()["id"], unassigned.json()["id"]]
    assert set(actions[0]) == {
        "id", "retro_id", "description", "owner_id", "owner", "owner_state", "due_date",
        "status",
    }, "owner_state joined the shape in #23"
    assert actions[0]["owner"] == "Bob"
    assert actions[0]["retro_id"] == retro_id
    assert actions[0]["status"] == "open"
    assert actions[1]["owner_id"] is None and actions[1]["owner"] is None
    assert done.json()["id"] not in [a["id"] for a in actions]


@pytest.mark.asyncio
async def test_completing_an_action_after_publish_drops_it_from_open_actions(
    client, auth_headers, project_with_member, discussion_retro, advance_phase
):
    """#38: publishing does not freeze this one write, and the dashboard shows it."""
    retro_id = discussion_retro["retro"]["id"]
    action = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Write the postmortem"},
        headers=auth_headers,
    )
    await advance_phase(retro_id, "done")

    before = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert action.json()["id"] in [a["id"] for a in before.json()["open_actions"]]

    completed = await client.patch(
        f"/api/retros/{retro_id}/actions/{action.json()['id']}",
        json={"status": "done"},
        headers=auth_headers,
    )
    assert completed.status_code == 200, completed.text

    after = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert action.json()["id"] not in [a["id"] for a in after.json()["open_actions"]]


@pytest.mark.asyncio
async def test_an_unresolved_extracted_owner_name_is_shown_as_it_was_extracted(
    client, auth_headers, project_with_member, discussion_retro
):
    retro = await Retrospective.get(discussion_retro["retro"]["id"])
    retro.actions.append(
        Action(id="a-extracted", description="From the transcript", owner_name="  Dana Wu  ")
    )
    retro.actions.append(
        Action(id="a-departed", description="Left the team", owner_id=PydanticObjectId())
    )
    await retro.save()

    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    rows = {a["id"]: a for a in response.json()["open_actions"]}

    assert rows["a-extracted"]["owner"] == "Dana Wu", "stripped, not resolved"
    assert rows["a-departed"]["owner"] is None, "not a current member"
    assert rows["a-departed"]["owner_id"] is not None, "the reference is still there"


@pytest.mark.asyncio
async def test_open_actions_span_every_retrospective_newest_first(
    client, auth_headers, project, cycle, reveal, advance_phase
):
    ids = []
    for label in ("older", "newer"):
        retro = await reveal(cycle["id"])
        for phase in ("cluster", "vote", "discuss"):
            retro = await advance_phase(retro["id"], phase)
        created = await client.post(
            f"/api/retros/{retro['id']}/actions",
            json={"description": f"from the {label} retro"},
            headers=auth_headers,
        )
        ids.append(created.json()["id"])
        await client.post(f"/api/retros/{retro['id']}/summary/publish", headers=auth_headers)
        cycle = (
            await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
        ).json()

    response = await client.get(_url(project["id"]), headers=auth_headers)
    assert [a["id"] for a in response.json()["open_actions"]] == list(reversed(ids))


# --- privacy -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_dashboard_reveals_no_card_no_author_and_no_ballot(
    client, auth_headers, second_auth_headers, project_with_member, shared_cycle, add_card,
    reveal, advance_phase, add_cluster,
):
    await add_card(shared_cycle["id"], text="a thing I said in confidence")
    await add_card(shared_cycle["id"], headers=second_auth_headers, is_anonymous=True,
                   text="something anonymous")
    retro = await reveal(shared_cycle["id"])
    retro = await advance_phase(retro["id"], "cluster")
    cluster = await add_cluster(retro["id"])
    retro = await advance_phase(retro["id"], "vote")
    await client.post(
        f"/api/retros/{retro['id']}/votes",
        json={"cluster_ids": [cluster["id"]]},
        headers=second_auth_headers,
    )

    response = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    assert response.status_code == 200
    text = response.text

    for leaked in ("a thing I said in confidence", "something anonymous", cluster["id"]):
        assert leaked not in text, leaked
    for key in ("cards", "votes", "ballots", "cluster_ids", "submitted", "author_id"):
        if key == "submitted":
            continue  # submitted_members is a count, and is meant to be there
        assert key not in text, key
    assert "submitted_members" in text


@pytest.mark.asyncio
async def test_it_writes_nothing_and_creates_no_collection(
    client, auth_headers, project_with_member, discussion_retro
):
    from app.database import DOCUMENT_MODELS

    retro_id = discussion_retro["retro"]["id"]
    before = (await Retrospective.get(retro_id)).model_dump()
    cycles_before = await Cycle.find(Cycle.project_id == PydanticObjectId(project_with_member["id"])).to_list()

    first = await client.get(_url(project_with_member["id"]), headers=auth_headers)
    second = await client.get(_url(project_with_member["id"]), headers=auth_headers)

    assert first.json() == second.json()
    assert (await Retrospective.get(retro_id)).model_dump() == before
    assert len(
        await Cycle.find(Cycle.project_id == PydanticObjectId(project_with_member["id"])).to_list()
    ) == len(cycles_before)
    assert not any("dashboard" in model.__name__.lower() for model in DOCUMENT_MODELS)


@pytest.mark.asyncio
async def test_a_closed_cycle_with_no_retro_is_neither_current_nor_past(
    client, auth_headers, project, cycle
):
    stored = await Cycle.get(cycle["id"])
    stored.status = CLOSED
    await stored.save()

    response = await client.get(_url(project["id"]), headers=auth_headers)
    body = response.json()
    assert body["current_cycle"] is None
    assert body["past_retros"] == [], "there was no retrospective to list"
