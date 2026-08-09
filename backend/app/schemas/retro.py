from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class UpdatePhaseRequest(BaseModel):
    phase: Literal["reveal", "cluster", "vote", "discuss", "done"]


class RetroResponse(BaseModel):
    id: str
    cycle_id: str
    phase: str
    clusters: list[dict]
    votes: list[dict]
    topics: list[dict]
    decisions: list[dict]
    actions: list[dict]
    voting_results_opened_at: datetime | None
    transcript: str | None
    ai_suggestions: dict | None
    created_at: datetime
