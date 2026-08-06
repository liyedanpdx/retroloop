from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pydantic import Field

START = "start"
STOP = "stop"
CONTINUE = "continue"

CATEGORIES = (START, STOP, CONTINUE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackCard(Document):
    cycle_id: PydanticObjectId
    author_id: PydanticObjectId | None = None
    category: str
    text: str
    is_anonymous: bool = False
    cluster_id: str | None = None
    created_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "feedback_cards"
