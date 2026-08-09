from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClusterNameRequest(BaseModel):
    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class MoveCardRequest(BaseModel):
    cluster_id: str | None = None


class ClusterResponse(BaseModel):
    id: str
    name: str
    created_at: datetime


class SuggestClustersRequest(BaseModel):
    """An empty body, and only an empty body (#19).

    There are no fields on purpose and `extra="forbid"` keeps it that way: the
    caller does not get to supply a prompt, a model, a card list or anything
    else that would end up in a request to the proxy. A body carrying a field is
    a 422 rather than a field quietly ignored, because a client sending one
    believes it is doing something.
    """

    model_config = ConfigDict(extra="forbid")


class SuggestedCluster(BaseModel):
    name: str
    card_ids: list[str]


class ClusterSuggestionResponse(BaseModel):
    """A proposal, in the proxy's own order. Nothing here is stored (#19).

    Card ids rather than card text: the client already has the cards, and a
    grouping that repeated their text would be a second copy to keep in sync
    with the one #16 is rendering. `ungrouped_card_ids` is explicit so a card
    the model could not place is visibly unplaced rather than missing.
    """

    clusters: list[SuggestedCluster]
    ungrouped_card_ids: list[str]
