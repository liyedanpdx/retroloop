import pytest

UNKNOWN_ID = "507f1f77bcf86cd799439011"


async def _close(client, cycle_id, headers):
    return await client.patch(f"/api/cycles/{cycle_id}", json={"status": "closed"}, headers=headers)


@pytest.mark.asyncio
async def test_create_cycle(client, auth_headers, project, registered_user):
    resp = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "collecting"
    assert data["project_id"] == project["id"]
    assert data["created_by"] == registered_user["id"]
    assert data["closed_at"] is None


@pytest.mark.asyncio
async def test_create_cycle_when_one_is_open(client, auth_headers, project, cycle):
    resp = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_cycle_after_closing_the_previous_one(
    client, auth_headers, project, cycle
):
    close_resp = await _close(client, cycle["id"], auth_headers)
    assert close_resp.status_code == 200

    resp = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "collecting"


@pytest.mark.asyncio
async def test_open_cycle_rule_is_per_project(client, auth_headers, project, cycle):
    other = await client.post("/api/projects", json={"name": "Team Beta"}, headers=auth_headers)
    other_id = other.json()["id"]

    resp = await client.post(f"/api/projects/{other_id}/cycles", headers=auth_headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_cycle_as_plain_member(
    client, second_auth_headers, project_with_member
):
    resp = await client.post(
        f"/api/projects/{project_with_member['id']}/cycles", headers=second_auth_headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_cycle_as_non_member(client, second_auth_headers, project, second_user):
    resp = await client.post(
        f"/api/projects/{project['id']}/cycles", headers=second_auth_headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_close_cycle(client, auth_headers, cycle):
    resp = await _close(client, cycle["id"], auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "closed"
    assert data["closed_at"] is not None


@pytest.mark.asyncio
async def test_close_cycle_twice(client, auth_headers, cycle):
    first = await _close(client, cycle["id"], auth_headers)
    assert first.status_code == 200
    second = await _close(client, cycle["id"], auth_headers)
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_patch_to_unknown_status(client, auth_headers, cycle):
    resp = await client.patch(
        f"/api/cycles/{cycle['id']}", json={"status": "archived"}, headers=auth_headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_to_collecting_or_retro_is_rejected(client, auth_headers, cycle):
    for target in ["collecting", "retro"]:
        resp = await client.patch(
            f"/api/cycles/{cycle['id']}", json={"status": target}, headers=auth_headers
        )
        assert resp.status_code == 400, f"{target} should not be settable here"


@pytest.mark.asyncio
async def test_close_cycle_as_plain_member(
    client, auth_headers, second_auth_headers, project_with_member
):
    created = await client.post(
        f"/api/projects/{project_with_member['id']}/cycles", headers=auth_headers
    )
    resp = await _close(client, created.json()["id"], second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_close_a_cycle_in_retro_status(client, auth_headers, cycle):
    from app.models.cycle import RETRO, Cycle

    stored = await Cycle.get(cycle["id"])
    stored.status = RETRO
    await stored.save()

    resp = await _close(client, cycle["id"], auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


@pytest.mark.asyncio
async def test_a_cycle_in_retro_status_still_blocks_a_new_one(
    client, auth_headers, project, cycle
):
    from app.models.cycle import RETRO, Cycle

    stored = await Cycle.get(cycle["id"])
    stored.status = RETRO
    await stored.save()

    resp = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_cycles_newest_first(client, auth_headers, project, cycle):
    await _close(client, cycle["id"], auth_headers)
    second = await client.post(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    second_id = second.json()["id"]

    resp = await client.get(f"/api/projects/{project['id']}/cycles", headers=auth_headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert ids == [second_id, cycle["id"]]


@pytest.mark.asyncio
async def test_list_cycles_as_non_member(client, second_auth_headers, project, second_user):
    resp = await client.get(f"/api/projects/{project['id']}/cycles", headers=second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_cycle_as_member(
    client, auth_headers, second_auth_headers, project_with_member
):
    created = await client.post(
        f"/api/projects/{project_with_member['id']}/cycles", headers=auth_headers
    )
    cycle_id = created.json()["id"]

    resp = await client.get(f"/api/cycles/{cycle_id}", headers=second_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == cycle_id


@pytest.mark.asyncio
async def test_get_cycle_as_non_member(client, second_auth_headers, cycle, second_user):
    resp = await client.get(f"/api/cycles/{cycle['id']}", headers=second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unknown_and_malformed_ids(client, auth_headers, project):
    cases = [
        client.post(f"/api/projects/{UNKNOWN_ID}/cycles", headers=auth_headers),
        client.post("/api/projects/abc/cycles", headers=auth_headers),
        client.get(f"/api/projects/{UNKNOWN_ID}/cycles", headers=auth_headers),
        client.get("/api/projects/abc/cycles", headers=auth_headers),
        client.get(f"/api/cycles/{UNKNOWN_ID}", headers=auth_headers),
        client.get("/api/cycles/abc", headers=auth_headers),
        client.patch(
            f"/api/cycles/{UNKNOWN_ID}", json={"status": "closed"}, headers=auth_headers
        ),
        client.patch("/api/cycles/abc", json={"status": "closed"}, headers=auth_headers),
    ]
    for call in cases:
        resp = await call
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_authentication(client, project, cycle):
    unauthenticated = [
        client.post(f"/api/projects/{project['id']}/cycles"),
        client.get(f"/api/projects/{project['id']}/cycles"),
        client.get(f"/api/cycles/{cycle['id']}"),
        client.patch(f"/api/cycles/{cycle['id']}", json={"status": "closed"}),
    ]
    for call in unauthenticated:
        resp = await call
        assert resp.status_code in (401, 403)
