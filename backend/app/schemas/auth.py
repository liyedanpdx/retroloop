from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        # bcrypt only hashes the first 72 bytes and raises on more, rather
        # than truncating, so this has to be rejected before hash_password.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 bytes")
        return value


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
