from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

TalkingPoint = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TalkingPointsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    ] | None = None


class GeneratedTalkingPoints(BaseModel):
    points: Annotated[list[TalkingPoint], Field(min_length=3, max_length=5)]

    @field_validator("points")
    @classmethod
    def require_unique_points(cls, value: list[str]) -> list[str]:
        normalized = [point.casefold() for point in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Talking points must be unique")
        return value


class TalkingPointsResponse(GeneratedTalkingPoints):
    section_id: UUID
