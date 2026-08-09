"""Request and response shapes for transcript extraction (#10).

The status-code split #7, #8 and #9 set holds here too: the shape of a body is a
422 raised by pydantic before a handler runs, wrong phase is 400, an unknown or
malformed id is 404, the wrong caller is 403, and a missing `Authorization`
header is 401. The one 409 in this issue is a paste while an extraction is in
flight, which is a state conflict rather than anything about the body.

The three `Literal`s below are the only place the status and state strings are
written out besides the constants they mirror in `app/models/retro.py`.

The confirm body's per-item fields are all optional and default to `None`, so
the handler reads `model_fields_set` to tell "not sent" from "sent as null" —
exactly as #9's PATCHes do, and what makes clearing an owner or a due date
expressible at all.
"""

from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.retro import MAX_TRANSCRIPT_CHARS

ExtractionStatus = Literal["idle", "processing", "ready", "failed"]
ExtractionError = Literal["timeout", "upstream_error", "malformed_response"]
SuggestionState = Literal["pending", "confirmed", "rejected"]


def _strip_or_reject(value: str | None) -> str | None:
    """Whitespace-only is blank, and blank is a 422 rather than a stored space."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class TranscriptRequest(BaseModel):
    """The pasted meeting transcript, and nothing else.

    `max_length` is a body-shape rule, so a transcript over the cap is a 422 and
    not a 413. It is measured before the strip, which is the only reading under
    which "exactly `MAX_TRANSCRIPT_CHARS` is accepted" has one answer.
    """

    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        return _strip_or_reject(value)


class TranscriptAcceptedResponse(BaseModel):
    """The 202. The proxy has not been touched yet, so there is nothing else to say."""

    status: ExtractionStatus


class ConfirmDecisionItem(BaseModel):
    id: str
    text: str | None = Field(default=None, min_length=1)

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str | None) -> str | None:
        return _strip_or_reject(value)


class ConfirmActionItem(BaseModel):
    id: str
    description: str | None = Field(default=None, min_length=1)
    owner_id: PydanticObjectId | None = None
    due_date: datetime | None = None

    @field_validator("description")
    @classmethod
    def description_is_not_blank(cls, value: str | None) -> str | None:
        return _strip_or_reject(value)


class ConfirmRequest(BaseModel):
    """One review pass: what to keep, how to fix it, and what to discard.

    An empty body is legal and does nothing — the facilitator who read the list
    and kept none of it has not made an error.
    """

    decisions: list[ConfirmDecisionItem] = Field(default_factory=list)
    actions: list[ConfirmActionItem] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def each_id_appears_once(self) -> "ConfirmRequest":
        """The same id twice, or confirmed and rejected at once, has no meaning.

        A body-shape contradiction rather than a state one, so 422 — and caught
        before anything is written, like every other all-or-nothing rule here.
        """
        seen = [item.id for item in self.decisions]
        seen += [item.id for item in self.actions]
        seen += list(self.rejected)
        if len(seen) != len(set(seen)):
            raise ValueError("each suggestion id may appear once in a confirm body")
        return self


class ConfirmedDecisionResponse(BaseModel):
    suggestion_id: str
    created_id: str
    text: str


class ConfirmedActionResponse(BaseModel):
    suggestion_id: str
    created_id: str
    description: str
    owner_id: str | None
    owner_name: str | None
    due_date: datetime | None


class ConfirmResponse(BaseModel):
    """What was created, and what was discarded, for the pass that just ran."""

    decisions: list[ConfirmedDecisionResponse]
    actions: list[ConfirmedActionResponse]
    rejected: list[str]
