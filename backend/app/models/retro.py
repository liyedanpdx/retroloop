from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field

REVEAL = "reveal"
CLUSTER = "cluster"
VOTE = "vote"
DISCUSS = "discuss"
DONE = "done"

# The one place the order lives. Transition checks are derived from it.
PHASE_ORDER = (REVEAL, CLUSTER, VOTE, DISCUSS, DONE)


def next_phase(current: str) -> str | None:
    """The only phase that may follow `current`, or None at the end."""
    index = PHASE_ORDER.index(current)
    if index + 1 >= len(PHASE_ORDER):
        return None
    return PHASE_ORDER[index + 1]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Cluster(BaseModel):
    id: str
    label: str
    card_ids: list[PydanticObjectId] = Field(default_factory=list)


class Vote(BaseModel):
    user_id: PydanticObjectId
    cluster_id: str


class Topic(BaseModel):
    id: str
    cluster_id: str
    vote_count: int = 0


class Decision(BaseModel):
    id: str
    topic_id: str | None = None
    text: str


class Action(BaseModel):
    id: str
    topic_id: str | None = None
    description: str
    owner_id: PydanticObjectId | None = None
    owner_name: str | None = None
    status: str = "open"
    due_date: datetime | None = None


class Retrospective(Document):
    cycle_id: PydanticObjectId
    phase: str = REVEAL
    clusters: list[Cluster] = Field(default_factory=list)
    votes: list[Vote] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    transcript: str | None = None
    ai_suggestions: dict | None = None
    created_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "retrospectives"
