from datetime import datetime

from pydantic import BaseModel, Field

from app.models.retro import MAX_VOTES_PER_USER


class SubmitVoteRequest(BaseModel):
    """A whole ballot, 1 to `MAX_VOTES_PER_USER` cluster ids, repeats allowed.

    The length lives here rather than in the handler on purpose: a body-shape
    violation is a 422, while phase and state failures are 400 / 409. That split
    is the convention #7 set with its blank-name rule.

    An empty list is rejected because it is indistinguishable from not having
    voted, and with no retraction it would lock the member out permanently.
    """

    cluster_ids: list[str] = Field(min_length=1, max_length=MAX_VOTES_PER_USER)


class VoteResponse(BaseModel):
    user_id: str
    cluster_ids: list[str]
    submitted_at: datetime


class VoteResultRow(BaseModel):
    cluster_id: str
    name: str
    vote_count: int
    rank: int


class VoteResultsResponse(BaseModel):
    members_voted: int
    members_total: int
    total_votes: int
    results: list[VoteResultRow]
