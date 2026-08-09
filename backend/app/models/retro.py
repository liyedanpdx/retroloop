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

# Where a transcript extraction stands (#10). `idle` is never stored — it is what
# a retro with no `ai_suggestions` at all reads as — and the other three are the
# only values `ai_suggestions["status"]` ever holds. #17 polls this field.
EXTRACTION_STATUSES = ("idle", "processing", "ready", "failed")

# Where one AI suggestion stands. Nothing is deleted on confirm or reject, so the
# facilitator can see what became of every extracted line (#10).
SUGGESTION_STATES = ("pending", "confirmed", "rejected")

# About a two-hour meeting. A body-shape rule, so over it is 422 (#10).
MAX_TRANSCRIPT_CHARS = 100_000

# The hard ceiling on one proxy call. Without it a hung proxy would leave the
# retro stuck on `processing`, and a paste is refused while it is (#10).
PROXY_TIMEOUT_SECONDS = 60


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
    """Something the team talks about — usually a cluster, sometimes not (#9, #22).

    #9 generates one per cluster from `tally()` on entering `discuss`. #22 lets
    the facilitator add, rename, reorder and remove them afterwards, and the
    three optional-looking fields below are what that costs.

    `cluster_id` is nullable because a topic somebody raised in the room came
    from no cluster. `name_override` exists because a rename needs somewhere to
    live: a plain `name` field would let a topic and its cluster disagree with
    no way to tell which is stale, whereas an override is unambiguous — somebody
    chose this, and clearing it goes back to the cluster's name. A topic with no
    cluster must have one.

    `vote_count` stays the tally's snapshot and never moves. `rank` no longer
    means "vote order": it is the agenda order, defaulting to the tally's and
    editable afterwards, which is what #11 sorts the summary on and what the
    word already promised.
    """

    id: str
    cluster_id: str | None = None
    name_override: str | None = None
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
    # When the tally first became visible to anybody (#21). Set once, never
    # cleared and never moved: results visibility is monotonic, so a later
    # membership change cannot re-hide them or re-open withdrawal.
    # 这个 retro 是否还接受写入 (#34)。权威放在 retro 自己身上,不是 cycle 上:
    # MongoDB 是单机,没有多文档事务,所以「检查」和「写入」必须落在同一个
    # 文档上才可能原子。关闭 cycle 的操作先原子地封这里,再去动 cycle。
    writes_closed_at: datetime | None = None
    voting_results_opened_at: datetime | None = None
    transcript: str | None = None
    ai_suggestions: dict | None = None
    created_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "retrospectives"
