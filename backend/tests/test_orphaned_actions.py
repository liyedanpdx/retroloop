"""An action whose owner has left says so (#23).

#9 decided the storage question: the `owner_id` stays, nothing is reassigned,
nothing is deleted. This issue is only about saying it out loud — so every
assertion here is about what a response *shows*, and one of them checks that the
document underneath is untouched.
"""

import pytest

from app.models.retro import Retrospective
from app.models.user import User

ASSIGNED = "assigned"
ORPHANED = "orphaned"
UNASSIGNED = "unassigned"


@pytest.fixture
async def with_actions(client, auth_headers, second_user, project_with_member, discussion_retro):
    """One action owned by bob, one owned by nobody, on a shared project."""
    retro_id = discussion_retro["retro"]["id"]
    owned = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Pair on the flaky test", "owner_id": second_user["id"]},
        headers=auth_headers,
    )
    assert owned.status_code == 201, owned.text
    loose = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Nobody yet"},
        headers=auth_headers,
    )
    return {
        "retro_id": retro_id,
        "project_id": project_with_member["id"],
        "owned": owned.json(),
        "loose": loose.json(),
    }


async def _remove_bob(client, auth_headers, with_actions, second_user):
    response = await client.delete(
        f"/api/projects/{with_actions['project_id']}/members/{second_user['id']}",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text


def _states(actions: list[dict]) -> dict:
    return {action["description"]: action["owner_state"] for action in actions}


@pytest.mark.asyncio
async def test_the_three_states_are_distinguishable(client, auth_headers, with_actions):
    board = await client.get(f"/api/retros/{with_actions['retro_id']}", headers=auth_headers)

    assert board.status_code == 200, board.text
    states = _states(board.json()["actions"])
    assert states["Pair on the flaky test"] == ASSIGNED
    assert states["Nobody yet"] == UNASSIGNED, "never assigned is not the same as abandoned"


@pytest.mark.asyncio
async def test_removing_the_owner_marks_the_action_orphaned(
    client, auth_headers, second_user, with_actions
):
    await _remove_bob(client, auth_headers, with_actions, second_user)

    board = await client.get(f"/api/retros/{with_actions['retro_id']}", headers=auth_headers)
    states = _states(board.json()["actions"])
    assert states["Pair on the flaky test"] == ORPHANED
    assert states["Nobody yet"] == UNASSIGNED, "still a different thing"


@pytest.mark.asyncio
async def test_the_stored_action_is_not_touched(
    client, auth_headers, second_user, with_actions
):
    """#9's call stands: the record of who it was is not rewritten."""
    before = await Retrospective.get(with_actions["retro_id"])
    stored = next(a for a in before.actions if a.description == "Pair on the flaky test")
    owner_before = stored.owner_id

    await _remove_bob(client, auth_headers, with_actions, second_user)

    after = await Retrospective.get(with_actions["retro_id"])
    still = next(a for a in after.actions if a.description == "Pair on the flaky test")
    assert still.owner_id == owner_before, "not cleared"
    assert len(after.actions) == len(before.actions), "not deleted"
    assert "owner_state" not in still.model_dump(), "derived on read, never stored"


@pytest.mark.asyncio
async def test_the_summary_says_so_too(
    client, auth_headers, second_user, with_actions
):
    await _remove_bob(client, auth_headers, with_actions, second_user)

    summary = await client.get(
        f"/api/retros/{with_actions['retro_id']}/summary", headers=auth_headers
    )
    assert summary.status_code == 200, summary.text
    orphan = next(
        row for row in summary.json()["actions"] if row["description"] == "Pair on the flaky test"
    )
    assert orphan["owner_state"] == ORPHANED
    assert orphan["owner"] is None, "#11 already refused to name a former member"
    assert orphan["owner_id"] is not None, "the reference is still there to reassign from"


@pytest.mark.asyncio
async def test_a_published_summary_still_says_so(
    client, auth_headers, second_user, with_actions
):
    """A commitment nobody is left to keep is what a team reads a summary for."""
    published = await client.post(
        f"/api/retros/{with_actions['retro_id']}/summary/publish", headers=auth_headers
    )
    assert published.status_code == 200, published.text

    await _remove_bob(client, auth_headers, with_actions, second_user)

    summary = await client.get(
        f"/api/retros/{with_actions['retro_id']}/summary", headers=auth_headers
    )
    orphan = next(
        row for row in summary.json()["actions"] if row["description"] == "Pair on the flaky test"
    )
    assert orphan["owner_state"] == ORPHANED


@pytest.mark.asyncio
async def test_the_dashboard_lists_them_for_the_facilitator(
    client, auth_headers, second_user, with_actions
):
    await _remove_bob(client, auth_headers, with_actions, second_user)

    dashboard = await client.get(
        f"/api/projects/{with_actions['project_id']}/dashboard", headers=auth_headers
    )
    assert dashboard.status_code == 200, dashboard.text
    states = _states(dashboard.json()["open_actions"])
    assert states["Pair on the flaky test"] == ORPHANED
    assert states["Nobody yet"] == UNASSIGNED

    orphans = [
        row for row in dashboard.json()["open_actions"] if row["owner_state"] == ORPHANED
    ]
    assert len(orphans) == 1, "the facilitator's to-do list is this list, filtered"


@pytest.mark.asyncio
async def test_reassigning_clears_the_flag(client, auth_headers, second_user, with_actions):
    """Reassignment already worked (#9); this only proves the flag follows it."""
    await _remove_bob(client, auth_headers, with_actions, second_user)

    me = await User.find_one(User.email == "alice@example.com")
    patched = await client.patch(
        f"/api/retros/{with_actions['retro_id']}/actions/{with_actions['owned']['id']}",
        json={"owner_id": str(me.id)},
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["owner_state"] == ASSIGNED

    board = await client.get(f"/api/retros/{with_actions['retro_id']}", headers=auth_headers)
    assert _states(board.json()["actions"])["Pair on the flaky test"] == ASSIGNED


@pytest.mark.asyncio
async def test_every_action_response_carries_the_field(
    client, auth_headers, second_user, discussion_retro
):
    """One shape. A field that appears on some responses is the #10 defect again."""
    retro_id = discussion_retro["retro"]["id"]

    created = await client.post(
        f"/api/retros/{retro_id}/actions",
        json={"description": "Write it up", "owner_id": second_user["id"]},
        headers=auth_headers,
    )
    assert created.status_code == 201
    assert created.json()["owner_state"] == ASSIGNED

    patched = await client.patch(
        f"/api/retros/{retro_id}/actions/{created.json()['id']}",
        json={"status": "done"},
        headers=auth_headers,
    )
    assert patched.json()["owner_state"] == ASSIGNED

    board = await client.get(f"/api/retros/{retro_id}", headers=auth_headers)
    assert all("owner_state" in action for action in board.json()["actions"])


@pytest.mark.asyncio
async def test_an_ai_extracted_owner_that_matched_nobody_is_unassigned(
    client, auth_headers, discussion_retro
):
    """#10's best-effort miss is not an abandoned commitment."""
    retro = await Retrospective.get(discussion_retro["retro"]["id"])
    from app.models.retro import Action

    retro.actions.append(
        Action(id="a-extracted", description="From the transcript", owner_name="Dana Wu")
    )
    await retro.save()

    board = await client.get(f"/api/retros/{retro.id}", headers=auth_headers)
    extracted = next(
        row for row in board.json()["actions"] if row["description"] == "From the transcript"
    )
    assert extracted["owner_state"] == UNASSIGNED
    assert extracted["owner_name"] == "Dana Wu", "who the meeting named is still recorded"
