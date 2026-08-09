"""The refresh cookie: one place that knows its name and its attributes (#30).

`_docs/decisions.md`, "Token storage": the access token lives in memory in the
browser and the refresh token lives in an httpOnly cookie, because
`localStorage` is readable by any script that gets onto the page. That is only
true if the refresh token is never handed to JavaScript at all — so it is not in
the login response body either, and `POST /api/auth/refresh` reads it from the
cookie rather than from a request body.

Setting and clearing are together here because they have to agree. A browser
matches a cookie for deletion on name, path and domain; a `delete_cookie` that
disagrees with the `set_cookie` about the path leaves the old one in place and
logout silently does nothing.
"""

from fastapi import Response

from app.config import settings
from app.services.auth import REFRESH_TOKEN_EXPIRE_DAYS

REFRESH_COOKIE = "refresh_token"

# Scoped to the auth routes, so the token is not attached to every API call the
# app makes. `/api/auth/refresh` and `/api/auth/logout` are the only two
# endpoints that have any use for it.
REFRESH_COOKIE_PATH = "/api/auth"

REFRESH_COOKIE_MAX_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


def set_refresh_cookie(response: Response, token: str) -> None:
    """`Secure` and `SameSite` come from settings, not from a literal here.

    Over plain HTTP on localhost a `Secure` cookie is dropped by the browser, so
    development would be broken by hard-coding it on; in production, leaving it
    off would put the refresh token on the wire in clear. It is a deployment
    fact, so it is configuration — `COOKIE_SECURE=true` alongside the HTTPS
    origin.
    """
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def clear_refresh_cookie(response: Response) -> None:
    """Expire it, with the same name, path and flags it was set with."""
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )
