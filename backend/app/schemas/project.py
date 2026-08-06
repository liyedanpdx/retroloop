from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: Literal["facilitator", "member"] = "member"


class MemberResponse(BaseModel):
    user_id: str
    role: str
    joined_at: datetime


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    members: list[MemberResponse]
    created_at: datetime
    created_by: str
