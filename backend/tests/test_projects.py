import pytest

UNKNOWN_ID = "507f1f77bcf86cd799439011"


@pytest.mark.asyncio
async def test_create_project(client, auth_headers, registered_user):
    resp = await client.post(
        "/api/projects",
        json={"name": "Team Alpha", "description": "our retro project"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Team Alpha"
    assert data["description"] == "our retro project"
    assert data["created_by"] == registered_user["id"]
    assert "id" in data


@pytest.mark.asyncio
async def test_creator_becomes_facilitator(client, project, registered_user):
    assert len(project["members"]) == 1
    member = project["members"][0]
    assert member["user_id"] == registered_user["id"]
    assert member["role"] == "facilitator"


@pytest.mark.asyncio
async def test_create_project_without_description(client, auth_headers):
    resp = await client.post("/api/projects", json={"name": "No Desc"}, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["description"] is None


@pytest.mark.asyncio
async def test_create_project_missing_name(client, auth_headers):
    resp = await client.post("/api/projects", json={}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_project_blank_name(client, auth_headers):
    for blank in ["", "   "]:
        resp = await client.post("/api/projects", json={"name": blank}, headers=auth_headers)
        assert resp.status_code == 422, f"blank name {blank!r} should be rejected"


@pytest.mark.asyncio
async def test_list_projects_returns_only_own(
    client, auth_headers, second_auth_headers, project
):
    await client.post("/api/projects", json={"name": "Bob Project"}, headers=second_auth_headers)

    alice_resp = await client.get("/api/projects", headers=auth_headers)
    assert alice_resp.status_code == 200
    alice_names = [p["name"] for p in alice_resp.json()]
    assert alice_names == ["Team Alpha"]

    bob_resp = await client.get("/api/projects", headers=second_auth_headers)
    bob_names = [p["name"] for p in bob_resp.json()]
    assert bob_names == ["Bob Project"]


@pytest.mark.asyncio
async def test_get_project_as_member(client, auth_headers, project):
    resp = await client.get(f"/api/projects/{project['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == project["id"]


@pytest.mark.asyncio
async def test_get_project_as_non_member(client, second_auth_headers, project):
    resp = await client.get(f"/api/projects/{project['id']}", headers=second_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_project_unknown_id(client, auth_headers):
    resp = await client.get(f"/api/projects/{UNKNOWN_ID}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_project_malformed_id(client, auth_headers):
    resp = await client.get("/api/projects/abc", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_member(client, auth_headers, project, second_user):
    resp = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    members = resp.json()
    assert len(members) == 2
    added = [m for m in members if m["user_id"] == second_user["id"]][0]
    assert added["role"] == "member"


@pytest.mark.asyncio
async def test_added_member_can_read_the_project(
    client, auth_headers, second_auth_headers, project, second_user
):
    await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    resp = await client.get(f"/api/projects/{project['id']}", headers=second_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_add_member_unknown_email(client, auth_headers, project):
    resp = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "nobody@example.com"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_member_duplicate(client, auth_headers, project, second_user):
    payload = {"email": "bob@example.com"}
    first = await client.post(
        f"/api/projects/{project['id']}/members", json=payload, headers=auth_headers
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/projects/{project['id']}/members", json=payload, headers=auth_headers
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_add_member_invalid_role(client, auth_headers, project, second_user):
    resp = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com", "role": "admin"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_member_as_non_facilitator(
    client, auth_headers, second_auth_headers, project, second_user
):
    await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    await client.post(
        "/api/auth/register",
        json={"email": "carol@example.com", "password": "secret789", "display_name": "Carol"},
    )
    resp = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "carol@example.com"},
        headers=second_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_member_as_non_member(client, second_auth_headers, project):
    resp = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=second_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_remove_member(client, auth_headers, project, second_user):
    await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    resp = await client.delete(
        f"/api/projects/{project['id']}/members/{second_user['id']}", headers=auth_headers
    )
    assert resp.status_code == 200
    members = resp.json()
    assert len(members) == 1
    assert second_user["id"] not in [m["user_id"] for m in members]


@pytest.mark.asyncio
async def test_facilitator_cannot_remove_themselves(
    client, auth_headers, project, registered_user
):
    resp = await client.delete(
        f"/api/projects/{project['id']}/members/{registered_user['id']}", headers=auth_headers
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_remove_user_who_is_not_a_member(client, auth_headers, project, second_user):
    resp = await client.delete(
        f"/api/projects/{project['id']}/members/{second_user['id']}", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_as_non_facilitator(
    client, auth_headers, second_auth_headers, project, second_user, registered_user
):
    await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    resp = await client.delete(
        f"/api/projects/{project['id']}/members/{registered_user['id']}",
        headers=second_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_endpoints_require_authentication(client, project, second_user):
    unauthenticated = [
        client.post("/api/projects", json={"name": "X"}),
        client.get("/api/projects"),
        client.get(f"/api/projects/{project['id']}"),
        client.post(
            f"/api/projects/{project['id']}/members", json={"email": "bob@example.com"}
        ),
        client.delete(f"/api/projects/{project['id']}/members/{second_user['id']}"),
    ]
    for call in unauthenticated:
        resp = await call
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_no_password_hash_in_responses(client, auth_headers, project, second_user):
    add_resp = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "bob@example.com"},
        headers=auth_headers,
    )
    detail_resp = await client.get(f"/api/projects/{project['id']}", headers=auth_headers)
    for resp in (add_resp, detail_resp):
        assert "hashed_password" not in resp.text
