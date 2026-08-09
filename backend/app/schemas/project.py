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


class UpdateProjectRequest(BaseModel):
    """Rename and re-describe. Both optional, neither blank (#32)."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class UpdateMemberRoleRequest(BaseModel):
    role: Literal["facilitator", "member"]


class ArchiveProjectRequest(BaseModel):
    """`true` archives, `false` restores. One field, so neither is a guess."""

    archived: bool


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
    archived_at: datetime | None
