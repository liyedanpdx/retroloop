"""Request and response shapes for the discussion phase (#9).

The status-code split #7 and #8 set holds here: anything wrong with the *shape*
of a body is a 422 raised by pydantic before a handler runs — the two status
enums are `Literal`s, and `text` / `description` get `min_length=1` plus a strip
validator, exactly as `ClusterNameRequest` does it. Wrong phase is 400, an
unknown or malformed id is 404, and a caller who may not do this — or may not
touch this field — is 403.

Every PATCH field is optional and defaults to `None`, so handlers read
`model_fields_set` to tell "not sent" from "sent as null". That is what makes
clearing `topic_id` or `due_date` expressible at all.
"""

from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator

TopicStatus = Literal["pending", "discussed", "skipped", "deferred"]
ActionStatus = Literal["open", "done"]


def _strip_or_reject(value: str | None) -> str | None:
    """Whitespace-only is blank, and blank is a 422 rather than a stored space."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class UpdateTopicRequest(BaseModel):
    """`status` and `notes` are the only mutable fields on a topic.

    `cluster_id`, `vote_count` and `rank` are a snapshot of the tally and are
    absent on purpose; pydantic ignores them if sent, so an attempt to move a
    topic's rank quietly changes nothing rather than half-succeeding.
    """

    status: TopicStatus | None = None
    notes: str | None = None


class CreateDecisionRequest(BaseModel):
    topic_id: str | None = None
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        return _strip_or_reject(value)


class UpdateDecisionRequest(BaseModel):
    topic_id: str | None = None
    text: str | None = Field(default=None, min_length=1)
    is_confirmed: bool | None = None

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str | None) -> str | None:
        return _strip_or_reject(value)


class CreateActionRequest(BaseModel):
    topic_id: str | None = None
    description: str = Field(min_length=1)
    owner_id: PydanticObjectId | None = None
    due_date: datetime | None = None

    @field_validator("description")
    @classmethod
    def description_is_not_blank(cls, value: str) -> str:
        return _strip_or_reject(value)


class UpdateActionRequest(BaseModel):
    topic_id: str | None = None
    description: str | None = Field(default=None, min_length=1)
    owner_id: PydanticObjectId | None = None
    status: ActionStatus | None = None
    due_date: datetime | None = None

    @field_validator("description")
    @classmethod
    def description_is_not_blank(cls, value: str | None) -> str | None:
        return _strip_or_reject(value)


class TopicResponse(BaseModel):
    id: str
    cluster_id: str
    name: str
    vote_count: int
    rank: int
    status: str
    notes: str


class DecisionResponse(BaseModel):
    id: str
    topic_id: str | None
    text: str
    is_confirmed: bool


class ActionResponse(BaseModel):
    """`owner_name` is #10's field, but it is always present here.

    #9 never writes it, so on an ordinary action it serialises as null. It is
    still emitted, because the shape of an action must not depend on the value
    of one of its fields: `GET /api/retros/{id}` has always shown `owner_name`,
    and a facilitator who `POST`s or `PATCH`es an action has to get back the
    same keys for the same object. Excluding the key when it is null was the
    defect #10 exists to remove — it made the response shape vary per action.
    """

    id: str
    topic_id: str | None
    description: str
    owner_id: str | None
    owner_name: str | None = None
    # `unassigned` | `assigned` | `orphaned` (#23). Derived from the current
    # member list on every read; nothing about it is stored on the action.
    owner_state: str
    status: str
    due_date: datetime | None
