import pytest


@pytest.mark.asyncio
async def test_register_success(client):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "bob@example.com", "password": "pass1234", "display_name": "Bob"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "bob@example.com"
    assert data["display_name"] == "Bob"
    assert "id" in data
    assert "password" not in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client, registered_user):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "other", "display_name": "Alice2"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_login_success(client, registered_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "refresh_token" not in data, "it is in the cookie now, and only there (#30)"


@pytest.mark.asyncio
async def test_login_wrong_password(client, registered_user):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_email(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_success(client, registered_user):
    login_resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert login_resp.status_code == 200

    # No token is passed: the client kept the cookie login set, and sends it.
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_refresh_invalid_token(client):
    client.cookies.set("refresh_token", "garbage", path="/api/auth")
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_valid_token(client, auth_headers):
    resp = await client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "alice@example.com"
    assert data["display_name"] == "Alice"
    assert "id" in data
    assert "created_at" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_me_without_token(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code in (401, 403)
