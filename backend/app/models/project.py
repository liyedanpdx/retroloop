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
    # Archived, not deleted (#32). Nothing is cascaded and nothing is destroyed:
    # a project's cycles, retrospectives, feedback and actions are the record of
    # what a team did, and deleting them is not reversible by anybody.
    archived_at: datetime | None = None

    class Settings:
        name = "projects"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def facilitators(self) -> list["Member"]:
        return [member for member in self.members if member.role == FACILITATOR]

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
