"""The refresh cookie, its attributes, and logout (#30).

The attributes are asserted off the raw `set-cookie` header rather than off
`response.cookies`, because the flags are the point: an httpOnly cookie that
forgot `HttpOnly` still round-trips perfectly in a test client and is readable
by any script in a browser. Only the header shows what the browser will be told.
"""

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.config import settings
from app.services.auth import REFRESH_TOKEN_EXPIRE_DAYS, create_refresh_token
from app.services.cookies import REFRESH_COOKIE, REFRESH_COOKIE_PATH

LOGIN = {"email": "alice@example.com", "password": "secret123"}


def _set_cookie_header(response) -> str:
    headers = [value for key, value in response.headers.multi_items() if key == "set-cookie"]
    matching = [header for header in headers if header.startswith(f"{REFRESH_COOKIE}=")]
    assert len(matching) == 1, f"expected one refresh cookie, got {headers}"
    return matching[0]


def _attributes(header: str) -> dict:
    parts = [part.strip() for part in header.split(";")[1:]]
    attributes = {}
    for part in parts:
        key, _, value = part.partition("=")
        attributes[key.lower()] = value
    return attributes


# --- login sets it -----------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_an_httponly_refresh_cookie_and_returns_only_the_access_token(
    client, registered_user
):
    response = await client.post("/api/auth/login", json=LOGIN)

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"access_token", "token_type"}
    assert "refresh_token" not in response.text, "nothing JavaScript can read"

    header = _set_cookie_header(response)
    attributes = _attributes(header)
    assert "httponly" in attributes, "the whole point: script cannot read it"
    assert attributes["path"] == REFRESH_COOKIE_PATH, "not sent with every API call"
    assert attributes["samesite"].lower() == settings.cookie_samesite
    assert int(attributes["max-age"]) == REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    assert ("secure" in attributes) == settings.cookie_secure

    # The value really is the refresh token, not the access token by mistake.
    value = header.split(";")[0].split("=", 1)[1]
    assert jwt.decode(value, settings.jwt_refresh_secret, algorithms=["HS256"])["type"] == "refresh"


@pytest.mark.asyncio
async def test_a_secure_deployment_marks_the_cookie_secure(client, registered_user, monkeypatch):
    """The flag is configuration, so production and development differ by env."""
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "cookie_samesite", "none")

    response = await client.post("/api/auth/login", json=LOGIN)
    attributes = _attributes(_set_cookie_header(response))

    assert "secure" in attributes
    assert attributes["samesite"].lower() == "none"
    assert "httponly" in attributes


@pytest.mark.asyncio
async def test_a_failed_login_sets_no_cookie(client, registered_user):
    response = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert REFRESH_COOKIE not in response.cookies
    assert REFRESH_COOKIE not in client.cookies


# --- refresh reads it --------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_works_from_the_cookie_alone(client, registered_user):
    await client.post("/api/auth/login", json=LOGIN)
    assert REFRESH_COOKIE in client.cookies, "the client is holding it, as a browser would"

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"access_token", "token_type"}

    # The new access token works, which is what the silent-refresh flow needs.
    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_refresh_ignores_a_refresh_token_sent_in_the_body(client, registered_user):
    """The body is not a way in. Only the cookie is."""
    token = create_refresh_token(registered_user["id"])
    response = await client.post("/api/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_every_bad_cookie_is_a_401(client, registered_user):
    cases = {
        "no cookie at all": None,
        "empty": "",
        "not a jwt": "garbage",
        "signed with the wrong secret": jwt.encode(
            {"sub": registered_user["id"], "type": "refresh"}, "not-the-secret", algorithm="HS256"
        ),
        "an access token": jwt.encode(
            {
                "sub": registered_user["id"],
                "type": "access",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.jwt_secret,
            algorithm="HS256",
        ),
        "expired": jwt.encode(
            {
                "sub": registered_user["id"],
                "type": "refresh",
                "exp": datetime.now(timezone.utc) - timedelta(days=1),
            },
            settings.jwt_refresh_secret,
            algorithm="HS256",
        ),
    }

    for name, value in cases.items():
        client.cookies.clear()
        if value is not None:
            client.cookies.set(REFRESH_COOKIE, value, path=REFRESH_COOKIE_PATH)
        response = await client.post("/api/auth/refresh")
        assert response.status_code == 401, f"{name}: {response.text}"
    client.cookies.clear()


@pytest.mark.asyncio
async def test_a_refresh_token_for_a_deleted_user_is_a_401(client, registered_user):
    from app.models.user import User

    await client.post("/api/auth/login", json=LOGIN)
    await (await User.get(registered_user["id"])).delete()

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


# --- logout clears it --------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_expires_the_cookie_and_refresh_stops_working(client, registered_user):
    await client.post("/api/auth/login", json=LOGIN)
    assert (await client.post("/api/auth/refresh")).status_code == 200

    response = await client.post("/api/auth/logout")
    assert response.status_code == 204

    header = _set_cookie_header(response)
    attributes = _attributes(header)
    value = header.split(";")[0].split("=", 1)[1].strip('"')
    assert value == "", f"cleared, not reissued — got {value!r}"
    assert attributes["path"] == REFRESH_COOKIE_PATH, "same path, or the browser keeps the old one"
    assert "httponly" in attributes
    assert int(attributes.get("max-age", 0)) == 0 or "1970" in attributes.get("expires", "")

    assert REFRESH_COOKIE not in client.cookies, "the client dropped it"
    assert (await client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_logout_is_safe_to_call_repeatedly_and_needs_no_token(client, registered_user):
    for _ in range(3):
        response = await client.post("/api/auth/logout")
        assert response.status_code == 204, response.text
        assert response.content == b""


@pytest.mark.asyncio
async def test_logout_does_not_end_other_sessions(client, registered_user):
    """No revocation list, by design — logout is a cookie, not server state."""
    await client.post("/api/auth/login", json=LOGIN)
    other = client.cookies[REFRESH_COOKIE]

    await client.post("/api/auth/logout")
    client.cookies.set(REFRESH_COOKIE, other, path=REFRESH_COOKIE_PATH)

    assert (await client.post("/api/auth/refresh")).status_code == 200, (
        "server-side revocation is explicitly out of scope for #30"
    )
    client.cookies.clear()


# --- CORS --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credentialed_cors_names_one_origin_and_never_a_wildcard(client, registered_user):
    response = await client.post(
        "/api/auth/login", json=LOGIN, headers={"Origin": settings.frontend_origin}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin
    assert response.headers["access-control-allow-origin"] != "*"
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_a_preflight_from_another_origin_is_not_granted_credentials(client):
    response = await client.request(
        "OPTIONS",
        "/api/auth/login",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "http://evil.example"
    assert response.headers.get("access-control-allow-origin") != "*"
