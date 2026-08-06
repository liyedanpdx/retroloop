from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class UpdateCycleRequest(BaseModel):
    status: Literal["collecting", "retro", "closed"]


class CycleResponse(BaseModel):
    id: str
    project_id: str
    status: str
    created_at: datetime
    closed_at: datetime | None
    created_by: str
