from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pydantic import Field

COLLECTING = "collecting"
RETRO = "retro"
CLOSED = "closed"

ACTIVE_STATUSES = (COLLECTING, RETRO)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Cycle(Document):
    project_id: PydanticObjectId
    status: str = COLLECTING
    created_at: datetime = Field(default_factory=_now)
    closed_at: datetime | None = None
    created_by: PydanticObjectId

    class Settings:
        name = "cycles"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES
