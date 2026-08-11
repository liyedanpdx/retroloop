"""GET /api/actions/mine — one caller's open actions across every project
they belong to (#45).

Every seeded retro here is built by inserting `Cycle`/`Retrospective`
documents directly rather than walking reveal → cluster → vote → discuss
through the real API for each of several projects: this endpoint is a read
over documents that already exist, so what it needs from a test is data in
that shape, not a second exercise of #6-#9's phase machinery.
"""

import pytest
from beanie import PydanticObjectId

from app.models.cycle import Cycle
from app.models.retro import Action, Retrospective

URL = "/api/actions/mine"


async def _make_project(client, headers, name):
    resp = await client.post(
        "/api/projects", json={"name": name, "description": None}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_member(client, headers, project_id, email):
    resp = await client.post(
        f"/api/projects/{project_id}/members", json={"email": email}, headers=headers
    )
    assert resp.status_code == 201, resp.text


async def _seed_retro(project_id, creator_id, actions, *, cycle_status="retro"):
    cycle = Cycle(
        project_id=PydanticObjectId(project_id), status=cycle_status, created_by=creator_id
    )
    await cycle.insert()
    retro = Retrospective(cycle_id=cycle.id, phase="discuss", actions=actions)
    await retro.insert()
    return cycle, retro


@pytest.mark.asyncio
async def test_lists_open_actions_owned_by_the_caller_across_every_project(
    client, auth_headers, second_auth_headers, second_user, registered_user
):
    bob_id = PydanticObjectId(second_user["id"])
    alice_id = PydanticObjectId(registered_user["id"])

    alpha = await _make_project(client, auth_headers, "Team Alpha")
    await _add_member(client, auth_headers, alpha["id"], "bob@example.com")
    beta = await _make_project(client, auth_headers, "Team Beta")
    await _add_member(client, auth_headers, beta["id"], "bob@example.com")

    await _seed_retro(
        alpha["id"],
        alice_id,
        [
            Action(id="a1", description="Bob's, open, in Alpha", owner_id=bob_id, status="open"),
            Action(id="a2", description="Alice's, open, in Alpha", owner_id=alice_id, status="open"),
            Action(id="a3", description="Bob's, already done", owner_id=bob_id, status="done"),
            Action(id="a4", description="Unassigned in Alpha", owner_id=None, status="open"),
        ],
    )
    _, beta_retro = await _seed_retro(
        beta["id"],
        alice_id,
        [Action(id="b1", description="Bob's, open, in Beta", owner_id=bob_id, status="open")],
    )

    resp = await client.get(URL, headers=second_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert {a["description"] for a in body} == {
        "Bob's, open, in Alpha",
        "Bob's, open, in Beta",
    }
    beta_row = next(a for a in body if a["description"] == "Bob's, open, in Beta")
    assert beta_row["project_id"] == beta["id"]
    assert beta_row["project_name"] == "Team Beta"
    assert beta_row["retro_id"] == str(beta_retro.id)
    assert beta_row["id"] == "b1"
    assert beta_row["due_date"] is None
    assert set(beta_row) == {"id", "project_id", "project_name", "retro_id", "description", "due_date"}


@pytest.mark.asyncio
async def test_includes_actions_from_an_archived_project(
    client, auth_headers, second_auth_headers, second_user, registered_user
):
    """An action already assigned does not stop needing doing just because
    its project got archived (#32) -- archiving only stops new work."""
    alice_id = PydanticObjectId(registered_user["id"])
    bob_id = PydanticObjectId(second_user["id"])

    project = await _make_project(client, auth_headers, "Wound Down")
    await _add_member(client, auth_headers, project["id"], "bob@example.com")
    await _seed_retro(
        project["id"],
        alice_id,
        [Action(id="c1", description="Still open in an archived project", owner_id=bob_id, status="open")],
        cycle_status="closed",
    )

    archived = await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": True}, headers=auth_headers
    )
    assert archived.status_code == 200, archived.text

    resp = await client.get(URL, headers=second_auth_headers)
    assert resp.status_code == 200, resp.text
    assert [a["description"] for a in resp.json()] == ["Still open in an archived project"]


@pytest.mark.asyncio
async def test_empty_with_no_projects(client, outsider_auth_headers):
    resp = await client.get(URL, headers=outsider_auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_empty_with_projects_but_nothing_assigned(client, auth_headers, project):
    """Alice belongs to a project (as its facilitator) but owns no actions in it."""
    resp = await client.get(URL, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_never_returns_another_members_actions(
    client, auth_headers, second_auth_headers, second_user, registered_user
):
    """Bob is a member of Alice's project, but the action there is hers."""
    alice_id = PydanticObjectId(registered_user["id"])

    project = await _make_project(client, auth_headers, "Shared")
    await _add_member(client, auth_headers, project["id"], "bob@example.com")
    await _seed_retro(
        project["id"], alice_id, [Action(id="d1", description="Alice's only", owner_id=alice_id, status="open")]
    )

    resp = await client.get(URL, headers=second_auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_never_returns_actions_from_a_project_the_caller_does_not_belong_to(
    client, auth_headers, outsider_auth_headers, registered_user
):
    alice_id = PydanticObjectId(registered_user["id"])
    project = await _make_project(client, auth_headers, "Not Carol's")
    await _seed_retro(
        project["id"], alice_id, [Action(id="e1", description="Alice's", owner_id=alice_id, status="open")]
    )

    resp = await client.get(URL, headers=outsider_auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_requires_authentication(client):
    resp = await client.get(URL)
    assert resp.status_code == 401, resp.text
