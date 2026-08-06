from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _not_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("text must not be blank")
    return stripped


class CreateFeedbackRequest(BaseModel):
    category: Literal["start", "stop", "continue"]
    text: str = Field(min_length=1)
    is_anonymous: bool = False

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class UpdateFeedbackRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    is_anonymous: bool | None = None

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _not_blank(value)


class FeedbackResponse(BaseModel):
    id: str
    cycle_id: str
    author_id: str | None
    category: str
    text: str
    is_anonymous: bool
    cluster_id: str | None
    created_at: datetime
