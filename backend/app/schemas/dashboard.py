"""The project dashboard's read shape (#31), assembled for #14.

One request instead of the six #14 would otherwise make — members, cycles, the
current retro, past retros, actions — and one place where the privacy rules for
that aggregate are written down.

Two things are absent on purpose and must stay absent. There is no card text and
no per-member "has submitted" flag: `submitted_members` is a count, because a
list would tell the room who has not filled theirs in yet, and that is a
different product from the anonymous one #5 built. There is no ballot data at
all, for the reason `app/services/votes.py` gives.
"""

from datetime import datetime

from pydantic import BaseModel


class DashboardMember(BaseModel):
    """A member the UI can actually render, rather than an opaque id."""

    user_id: str
    display_name: str
    email: str
    role: str
    joined_at: datetime


class SubmissionProgress(BaseModel):
    """How many current members have put a card in this cycle, not which ones.

    A member who wrote five cards counts once. A member who has left does not
    count at all, so this can never exceed `total_members`. A member whose only
    cards are anonymous cannot be counted either — #5 erased the author
    reference on purpose, and #28 tracks the participation signal that would fix
    that without putting authorship back.
    """

    submitted_members: int
    total_members: int


class DashboardRetro(BaseModel):
    id: str
    phase: str


class DashboardCycle(BaseModel):
    id: str
    status: str
    created_at: datetime
    closed_at: datetime | None
    progress: SubmissionProgress
    retro: DashboardRetro | None


class PastRetro(BaseModel):
    """Enough to label a finished retro and link to it, and nothing more."""

    id: str
    cycle_id: str
    phase: str
    created_at: datetime
    closed_at: datetime | None


class OpenAction(BaseModel):
    """`owner` resolves the same way #11's summary resolves it, in that order.

    The current member's display name if the action names one, otherwise the
    best-effort `owner_name` #10 extracted, otherwise nothing. An action left
    behind by a member who has since been removed keeps its `owner_id` and shows
    the extracted name if it has one — #23 is where surfacing those properly
    lives.
    """

    id: str
    retro_id: str
    description: str
    owner_id: str | None
    owner: str | None
    # `unassigned` | `assigned` | `orphaned` (#23) — the facilitator's list of
    # things to reassign is this list, filtered.
    owner_state: str
    due_date: datetime | None
    status: str


class DashboardResponse(BaseModel):
    members: list[DashboardMember]
    current_cycle: DashboardCycle | None
    past_retros: list[PastRetro]
    open_actions: list[OpenAction]
