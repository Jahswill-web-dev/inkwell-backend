from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ArticleGoal(StrEnum):
    INFORM_AND_INSPIRE = "inform_and_inspire"
    EDUCATE_WITH_PRACTICAL_GUIDANCE = "educate_with_practical_guidance"
    PERSUADE_OR_CHANGE_A_PERSPECTIVE = "persuade_or_change_a_perspective"
    INSPIRE_READERS_TO_TAKE_ACTION = "inspire_readers_to_take_action"
    ENTERTAIN_WITH_A_COMPELLING_STORY = "entertain_with_a_compelling_story"


class ArticleFields(BaseModel):
    notes: str = Field(min_length=1, max_length=20_000)
    working_title: str = Field(min_length=1, max_length=200)
    target_audience: str = Field(min_length=1, max_length=500)
    article_goal: ArticleGoal

    @field_validator("notes", "working_title", "target_audience", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ArticleCreate(ArticleFields):
    pass


class ArticleUpdate(BaseModel):
    notes: str | None = Field(default=None, min_length=1, max_length=20_000)
    working_title: str | None = Field(default=None, min_length=1, max_length=200)
    target_audience: str | None = Field(default=None, min_length=1, max_length=500)
    article_goal: ArticleGoal | None = None

    @field_validator("notes", "working_title", "target_audience", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_non_null_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one article field must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Article fields cannot be null")
        return self


class ArticleResponse(ArticleFields):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class ArticleListResponse(BaseModel):
    items: list[ArticleResponse]
    total: int
    offset: int
    limit: int
