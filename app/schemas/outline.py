from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.brief import BriefText


class OutlineSection(BaseModel):
    heading: BriefText
    purpose: BriefText
    key_points: Annotated[list[BriefText], Field(min_length=1, max_length=5)]


class GeneratedOutline(BaseModel):
    sections: Annotated[list[OutlineSection], Field(min_length=3, max_length=10)]


class ArticleOutlineSection(OutlineSection):
    id: UUID


class ArticleOutlineSectionUpdate(OutlineSection):
    id: UUID | None = None


class ArticleOutlineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: Annotated[list[ArticleOutlineSectionUpdate], Field(min_length=3, max_length=10)]

    @model_validator(mode="after")
    def require_unique_ids(self) -> Self:
        ids = [section.id for section in self.sections if section.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Outline section IDs must be unique")
        return self


class ArticleOutlineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    article_id: UUID
    sections: Annotated[list[ArticleOutlineSection], Field(min_length=3, max_length=10)]
    model_id: str
    prompt_version: str
    input_token_count: int | None
    output_token_count: int | None
    generation_duration_ms: int
    is_stale: bool
    created_at: datetime
    updated_at: datetime
