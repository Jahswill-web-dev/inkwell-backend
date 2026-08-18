from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BriefText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
BriefList = Annotated[list[BriefText], Field(min_length=1)]


class BriefSeo(BaseModel):
    suggested_titles: Annotated[list[BriefText], Field(min_length=3, max_length=5)]
    primary_keyword: BriefText
    secondary_keywords: Annotated[list[BriefText], Field(max_length=8)]
    meta_description: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
    ]


class BriefSeoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_titles: Annotated[list[BriefText], Field(min_length=3, max_length=5)] | None = None
    primary_keyword: BriefText | None = None
    secondary_keywords: Annotated[list[BriefText], Field(max_length=8)] | None = None
    meta_description: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
    ] | None = None

    @model_validator(mode="after")
    def require_non_null_field(self) -> Self:
        return _require_non_null_update(self)


class GeneratedBrief(BaseModel):
    summary: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
    ]
    core_angle: BriefText
    audience_insights: Annotated[list[BriefText], Field(min_length=1, max_length=6)]
    tone_and_style: BriefText
    key_takeaways: Annotated[list[BriefText], Field(min_length=3, max_length=8)]
    evidence_gaps: Annotated[list[BriefText], Field(max_length=8)]
    call_to_action: BriefText
    seo: BriefSeo


class ArticleBriefUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
    ] | None = None
    core_angle: BriefText | None = None
    audience_insights: Annotated[list[BriefText], Field(min_length=1, max_length=6)] | None = None
    tone_and_style: BriefText | None = None
    key_takeaways: Annotated[list[BriefText], Field(min_length=3, max_length=8)] | None = None
    evidence_gaps: Annotated[list[BriefText], Field(max_length=8)] | None = None
    call_to_action: BriefText | None = None
    seo: BriefSeoUpdate | None = None

    @model_validator(mode="after")
    def require_non_null_field(self) -> Self:
        return _require_non_null_update(self)


class ArticleBriefResponse(GeneratedBrief):
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


def _require_non_null_update[ModelT: BaseModel](model: ModelT) -> ModelT:
    if not model.model_fields_set:
        raise ValueError("At least one field must be provided")
    if any(getattr(model, field) is None for field in model.model_fields_set):
        raise ValueError("Fields cannot be null")
    return model
