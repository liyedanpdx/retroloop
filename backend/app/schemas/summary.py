from datetime import datetime

from pydantic import BaseModel


class SummaryTopic(BaseModel):
    id: str
    cluster_id: str | None
    name: str
    vote_count: int
    rank: int
    status: str
    notes: str


# Whether this entry started life as a draft pulled out of the meeting text
# (#10) rather than as something somebody typed. The summary reads the same
# either way, and a reader who cannot tell the two apart has no way to know
# whether the extraction they ran actually landed. It says where it came from,
# not that a machine decided it — a facilitator confirmed every one of these.
class SummaryDecision(BaseModel):
    id: str
    topic_id: str | None
    topic: str | None
    text: str
    from_transcript: bool = False


class SummaryAction(BaseModel):
    id: str
    topic_id: str | None
    topic: str | None
    description: str
    owner_id: str | None
    owner: str | None
    from_transcript: bool = False
    # `unassigned` | `assigned` | `orphaned` (#23). A published summary still
    # says which, because a commitment nobody is left to keep is exactly the
    # thing a team needs to notice when they read it back.
    owner_state: str
    due_date: datetime | None
    status: str


class SummaryParticipation(BaseModel):
    total_members: int
    submitted_feedback: int
    voted: int


class SummaryFeedbackCard(BaseModel):
    id: str
    category: str
    text: str
    is_anonymous: bool
    cluster_id: str | None
    author_id: str | None
    created_at: datetime


class SummaryResponse(BaseModel):
    topics: list[SummaryTopic]
    decisions: list[SummaryDecision]
    actions: list[SummaryAction]
    participation: SummaryParticipation
    feedback_cards: list[SummaryFeedbackCard]
