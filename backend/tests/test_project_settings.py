"""Rename, archive and role changes (#32).

Two rules carry this file. A project can never be left without a facilitator,
because there is no administrator above it to repair one. And nothing is ever
deleted — archiving is reversible, and a project's cycles, retrospectives,
feedback and actions survive it untouched.
"""

import pytest

from app.models.cycle import Cycle
from app.models.feedback import FeedbackCard
from app.models.project import Project
from app.models.retro import Retrospective

UNKNOWN_ID = "507f1f77bcf86cd799439011"
MALFORMED_ID = "abc"


# --- rename -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_facilitator_renames_and_re_describes(client, auth_headers, project):
    response = await client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "  Team Beta  ", "description": "  new words  "},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Team Beta", "trimmed"
    assert response.json()["description"] == "  new words  ", "kept as typed"
    assert (await Project.get(project["id"])).name == "Team Beta"


@pytest.mark.asyncio
async def test_a_description_can_be_cleared_and_an_empty_body_changes_nothing(
    client, auth_headers, project
):
    cleared = await client.patch(
        f"/api/projects/{project['id']}", json={"description": None}, headers=auth_headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["description"] is None

    same = await client.patch(f"/api/projects/{project['id']}", json={}, headers=auth_headers)
    assert same.status_code == 200, "an empty body is a no-op, not a 422"
    assert same.json()["name"] == project["name"]


@pytest.mark.asyncio
async def test_a_blank_name_is_refused(client, auth_headers, project):
    response = await client.patch(
        f"/api/projects/{project['id']}", json={"name": "   "}, headers=auth_headers
    )
    assert response.status_code == 422
    assert (await Project.get(project["id"])).name == project["name"]


@pytest.mark.asyncio
async def test_only_a_facilitator_may_rename(
    client, second_auth_headers, outsider_auth_headers, project_with_member
):
    for headers, expected in (
        (second_auth_headers, 403),
        (outsider_auth_headers, 403),
        (None, 401),
    ):
        kwargs = {"headers": headers} if headers else {}
        response = await client.patch(
            f"/api/projects/{project_with_member['id']}", json={"name": "Mine now"}, **kwargs
        )
        assert response.status_code == expected

    assert (await Project.get(project_with_member["id"])).name == project_with_member["name"]


# --- archive ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archiving_is_reversible_and_destroys_nothing(
    client, auth_headers, project, cycle, add_card, reveal, advance_phase
):
    card = await add_card(cycle["id"])
    retro = await reveal(cycle["id"])
    for phase in ("cluster", "vote", "discuss"):
        retro = await advance_phase(retro["id"], phase)
    await client.post(f"/api/retros/{retro['id']}/summary/publish", headers=auth_headers)

    archived = await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": True}, headers=auth_headers
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None

    # Every dependent document is still there, byte for byte.
    assert await Project.get(project["id"]) is not None
    assert await Cycle.get(cycle["id"]) is not None
    assert await Retrospective.get(retro["id"]) is not None
    assert await FeedbackCard.get(card["id"]) is not None

    restored = await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": False}, headers=auth_headers
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


@pytest.mark.asyncio
async def test_archiving_is_idempotent_in_both_directions(client, auth_headers, project):
    first = await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": True}, headers=auth_headers
    )
    again = await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": True}, headers=auth_headers
    )
    assert (first.status_code, again.status_code) == (200, 200)
    assert first.json()["archived_at"] == again.json()["archived_at"], "not re-stamped"

    assert (
        await client.patch(
            f"/api/projects/{project['id']}/archive",
            json={"archived": False},
            headers=auth_headers,
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/api/projects/{project['id']}/archive",
            json={"archived": False},
            headers=auth_headers,
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_an_active_cycle_has_to_be_closed_first(client, auth_headers, project, cycle):
    """This is what makes "archived" mean nothing is in flight."""
    refused = await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": True}, headers=auth_headers
    )
    assert refused.status_code == 400
    assert "cycle" in refused.json()["detail"]
    assert (await Project.get(project["id"])).archived_at is None

    await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "closed"}, headers=auth_headers
    )
    assert (
        await client.patch(
            f"/api/projects/{project['id']}/archive",
            json={"archived": True},
            headers=auth_headers,
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_an_archived_project_is_read_only(
    client, auth_headers, second_auth_headers, project_with_member, second_user
):
    await client.patch(
        f"/api/projects/{project_with_member['id']}/archive",
        json={"archived": True},
        headers=auth_headers,
    )
    project_id = project_with_member["id"]

    for method, url, body in (
        ("patch", f"/api/projects/{project_id}", {"name": "Renamed"}),
        ("post", f"/api/projects/{project_id}/members", {"email": "carol@example.com"}),
        ("patch", f"/api/projects/{project_id}/members/{second_user['id']}", {"role": "facilitator"}),
        ("post", f"/api/projects/{project_id}/cycles", None),
    ):
        kwargs = {"headers": auth_headers}
        if body is not None:
            kwargs["json"] = body
        response = await getattr(client, method)(url, **kwargs)
        assert response.status_code == 400, f"{method} {url}: {response.text}"
        assert response.json()["detail"] in (
            "This project is archived",
            "This project is archived",
        )

    delete = await client.delete(
        f"/api/projects/{project_id}/members/{second_user['id']}", headers=auth_headers
    )
    assert delete.status_code == 400

    # Reading still works for everyone who could read it before.
    assert (await client.get(f"/api/projects/{project_id}", headers=auth_headers)).status_code == 200
    assert (
        await client.get(f"/api/projects/{project_id}/dashboard", headers=second_auth_headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_only_a_facilitator_may_archive(
    client, second_auth_headers, outsider_auth_headers, project_with_member
):
    for headers, expected in ((second_auth_headers, 403), (outsider_auth_headers, 403)):
        response = await client.patch(
            f"/api/projects/{project_with_member['id']}/archive",
            json={"archived": True},
            headers=headers,
        )
        assert response.status_code == expected
    assert (await Project.get(project_with_member["id"])).archived_at is None


# --- roles --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_facilitator_promotes_and_demotes(
    client, auth_headers, second_user, project_with_member
):
    promoted = await client.patch(
        f"/api/projects/{project_with_member['id']}/members/{second_user['id']}",
        json={"role": "facilitator"},
        headers=auth_headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert {row["user_id"]: row["role"] for row in promoted.json()}[second_user["id"]] == (
        "facilitator"
    )

    demoted = await client.patch(
        f"/api/projects/{project_with_member['id']}/members/{second_user['id']}",
        json={"role": "member"},
        headers=auth_headers,
    )
    assert demoted.status_code == 200
    assert {row["user_id"]: row["role"] for row in demoted.json()}[second_user["id"]] == "member"


@pytest.mark.asyncio
async def test_the_last_facilitator_cannot_be_demoted(
    client, auth_headers, registered_user, project_with_member
):
    """A project nobody can run is a project whose cycles nobody can close."""
    response = await client.patch(
        f"/api/projects/{project_with_member['id']}/members/{registered_user['id']}",
        json={"role": "member"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A project must always have a facilitator"
    stored = await Project.get(project_with_member["id"])
    assert stored.is_facilitator(stored.members[0].user_id)


@pytest.mark.asyncio
async def test_handing_over_and_stepping_back_is_possible(
    client, auth_headers, registered_user, second_user, project_with_member
):
    """Self-demotion is allowed once somebody else can run the project."""
    await client.patch(
        f"/api/projects/{project_with_member['id']}/members/{second_user['id']}",
        json={"role": "facilitator"},
        headers=auth_headers,
    )
    response = await client.patch(
        f"/api/projects/{project_with_member['id']}/members/{registered_user['id']}",
        json={"role": "member"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    stored = await Project.get(project_with_member["id"])
    assert len(stored.facilitators()) == 1
    assert not stored.is_facilitator(stored.members[0].user_id)

    # And the former facilitator really has lost the powers.
    assert (
        await client.patch(
            f"/api/projects/{project_with_member['id']}", json={"name": "Mine"}, headers=auth_headers
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_role_changes_need_a_facilitator_and_a_real_member(
    client, auth_headers, second_auth_headers, outsider_auth_headers, second_user,
    project_with_member,
):
    project_id = project_with_member["id"]
    url = f"/api/projects/{project_id}/members/{second_user['id']}"

    assert (await client.patch(url, json={"role": "facilitator"})).status_code == 401
    assert (
        await client.patch(url, json={"role": "facilitator"}, headers=second_auth_headers)
    ).status_code == 403
    assert (
        await client.patch(url, json={"role": "facilitator"}, headers=outsider_auth_headers)
    ).status_code == 403
    assert (
        await client.patch(url, json={"role": "owner"}, headers=auth_headers)
    ).status_code == 422

    for target in (UNKNOWN_ID, MALFORMED_ID):
        response = await client.patch(
            f"/api/projects/{project_id}/members/{target}",
            json={"role": "facilitator"},
            headers=auth_headers,
        )
        assert response.status_code == 404, target

    assert (
        await client.patch(
            f"/api/projects/{UNKNOWN_ID}/members/{second_user['id']}",
            json={"role": "facilitator"},
            headers=auth_headers,
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_nothing_touches_another_project(client, auth_headers, project):
    second = await client.post(
        "/api/projects", json={"name": "Team Beta", "description": None}, headers=auth_headers
    )
    assert second.status_code == 201, second.text

    await client.patch(
        f"/api/projects/{project['id']}/archive", json={"archived": True}, headers=auth_headers
    )
    await client.patch(
        f"/api/projects/{project['id']}", json={"name": "Renamed"}, headers=auth_headers
    )

    other = await client.get(f"/api/projects/{second.json()['id']}", headers=auth_headers)
    assert other.json()["archived_at"] is None
    assert other.json()["name"] == "Team Beta"
