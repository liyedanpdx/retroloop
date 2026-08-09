from datetime import datetime

from pydantic import BaseModel


class SummaryTopic(BaseModel):
    id: str
    cluster_id: str
    name: str
    vote_count: int
    rank: int
    status: str
    notes: str


class SummaryDecision(BaseModel):
    id: str
    topic_id: str | None
    topic: str | None
    text: str


class SummaryAction(BaseModel):
    id: str
    topic_id: str | None
    topic: str | None
    description: str
    owner_id: str | None
    owner: str | None
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
