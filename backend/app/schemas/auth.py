from datetime import datetime

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str


class RegisterResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AccessTokenResponse(BaseModel):
    """The only token shape a client ever sees (#30).

    There is no `TokenResponse` carrying a refresh token and no
    `RefreshRequest` carrying one back: the refresh token travels only in an
    httpOnly cookie, so neither direction has a body field for it.
    """

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    created_at: datetime
