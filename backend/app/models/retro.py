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

# The vote budget one member gets, spent in a single submission (#8).
MAX_VOTES_PER_USER = 3

# Where a topic stands in the discussion (#9). The first entry is the default a
# freshly generated topic starts on.
TOPIC_STATUSES = ("pending", "discussed", "skipped", "deferred")

# An action item is open until somebody does it (#9).
ACTION_STATUSES = ("open", "done")


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
    name: str
    created_at: datetime = Field(default_factory=_now)


class Vote(BaseModel):
    """One member's whole ballot, not one vote.

    Submission is atomic and cannot be retracted (`_docs/decisions.md`), so a
    member has at most one of these per retro. That is what makes "has Alice
    voted?" a presence check and a second submission a flat 409.

    Stacking is repetition: `["c1", "c1", "c1"]` is three votes on `c1`, and a
    cluster's count is how many times its id appears across every ballot.
    """

    user_id: PydanticObjectId
    cluster_ids: list[str]
    submitted_at: datetime = Field(default_factory=_now)


class Topic(BaseModel):
    """One cluster, promoted to something the team actually talks about (#9).

    Generated from `tally()` on entering `discuss`, never by hand. There is
    deliberately no `name`: the name is the cluster's, the cluster is in this
    same document, and cluster writes stopped when the `cluster` phase ended
    (#7) — so a stored copy could only duplicate a value that can no longer
    change. `app/services/discussion.py` resolves it into responses instead.

    `vote_count` and `rank` are a snapshot taken at generation, not a live view.
    Voting is over by then and there is no retraction (#8), so nothing can move
    them afterwards. Only `status` and `notes` are mutable.
    """

    id: str
    cluster_id: str
    vote_count: int = 0
    rank: int = 0
    status: str = TOPIC_STATUSES[0]
    notes: str = ""


class Decision(BaseModel):
    """Something the team settled on, optionally hung off a topic.

    `is_confirmed` is what separates an AI draft (#10) from a decision the
    facilitator stands behind; #11 publishes only the confirmed ones.
    """

    id: str
    topic_id: str | None = None
    text: str
    is_confirmed: bool = False


class Action(BaseModel):
    """A commitment with an owner, or without one until somebody takes it.

    `owner_name` belongs to #10's best-effort matching of AI-extracted owner
    strings. Nothing in #9 writes it or returns it — an action created through
    the discussion endpoints leaves it `None`.
    """

    id: str
    topic_id: str | None = None
    description: str
    owner_id: PydanticObjectId | None = None
    owner_name: str | None = None
    status: str = ACTION_STATUSES[0]
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
