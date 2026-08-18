from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.brief import BriefText


class OutlineSection(BaseModel):
    heading: BriefText
    purpose: BriefText
    key_points: Annotated[list[BriefText], Field(min_length=1, max_length=5)]


class GeneratedOutline(BaseModel):
    sections: Annotated[list[OutlineSection], Field(min_length=3, max_length=10)]


class ArticleOutlineUpdate(GeneratedOutline):
    model_config = ConfigDict(extra="forbid")


class ArticleOutlineResponse(GeneratedOutline):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    article_id: UUID
    model_id: str
    prompt_version: str
    input_token_count: int | None
    output_token_count: int | None
    generation_duration_ms: int
    is_stale: bool
    created_at: datetime
    updated_at: datetime
