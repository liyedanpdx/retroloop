from datetime import timedelta

from beanie.exceptions import RevisionIdWasChanged
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    UserResponse,
)
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.services.rate_limit import consume
from app.services.cookies import REFRESH_COOKIE, clear_refresh_cookie, set_refresh_cookie

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    hashed = hash_password(body.password)
    user = User(email=body.email, hashed_password=hashed, display_name=body.display_name)
    try:
        await user.insert()
    except (DuplicateKeyError, RevisionIdWasChanged):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    return RegisterResponse(id=str(user.id), email=user.email, display_name=user.display_name)


@router.post("/login", response_model=AccessTokenResponse)
async def login(body: LoginRequest, response: Response):
    """The access token in the body, the refresh token in an httpOnly cookie.

    The refresh token is deliberately not in the response either (#30). A token
    the page can read is a token an injected script can read, and putting it in
    the body only to ask the client not to keep it would be a convention rather
    than a boundary.
    """
    # 密码猜测比 AI 花钱更值得先挡住 (#27)。按邮箱计数,而不是按 IP:
    # 一个办公室共用一个出口 IP,按 IP 限流会把整个团队一起锁掉,而攻击者换 IP
    # 比换目标邮箱容易得多。
    await consume(
        f"login:{body.email.lower()}",
        settings.login_attempts_per_15_minutes,
        timedelta(minutes=15),
        "Too many sign-in attempts for this account. Try again later.",
    )

    user = await User.find_one(User.email == body.email)
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    set_refresh_cookie(response, create_refresh_token(str(user.id)))
    return AccessTokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(request: Request):
    """A new access token, from the cookie the browser attached by itself.

    One 401 for every failure — no cookie, a forged one, an expired one, a
    refresh token whose user has since been deleted. They are the same event to
    a client: log in again.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_id = decode_refresh_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = await User.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return AccessTokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    """Expire the cookie. No body, no auth, and safe to call again.

    Requiring a valid access token here would make logging out impossible in the
    one situation it matters most — a session that has already gone wrong. There
    is nothing to protect: the worst a forged call can do is expire a cookie the
    caller already holds.
    """
    clear_refresh_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )
