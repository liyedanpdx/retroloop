from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field

FACILITATOR = "facilitator"
MEMBER = "member"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Member(BaseModel):
    user_id: PydanticObjectId
    role: str = MEMBER
    joined_at: datetime = Field(default_factory=_now)


class Project(Document):
    name: str
    description: str | None = None
    members: list[Member] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    created_by: PydanticObjectId

    class Settings:
        name = "projects"

    def member_for(self, user_id: PydanticObjectId) -> Member | None:
        for member in self.members:
            if member.user_id == user_id:
                return member
        return None

    def is_member(self, user_id: PydanticObjectId) -> bool:
        return self.member_for(user_id) is not None

    def is_facilitator(self, user_id: PydanticObjectId) -> bool:
        member = self.member_for(user_id)
        return member is not None and member.role == FACILITATOR
