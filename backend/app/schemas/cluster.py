from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
