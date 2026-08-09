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
    # Who took part in this cycle, whatever they wrote and whether or not it was
    # anonymous (#28). One id per member, at most once, and deliberately not on
    # the card: this says "Bob submitted something", never which something.
    #
    # It lives on the cycle rather than the card because a marker on an
    # anonymous card would be the author reference #5 erased, wearing a
    # different name. It is never returned by any endpoint.
    participants: list[PydanticObjectId] = Field(default_factory=list)

    class Settings:
        name = "cycles"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES
