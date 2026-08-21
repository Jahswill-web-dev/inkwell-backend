from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.brief import BriefText

ChecklistId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DraftChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ChecklistId
    label: BriefText
    completed: bool


class ArticleDraftSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    outline_section_id: UUID | None
    title: BriefText
    goal: BriefText
    checklist: list[DraftChecklistItem]
    editor_state: Annotated[str, StringConstraints(min_length=1)]

    @field_validator("editor_state")
    @classmethod
    def validate_editor_state(cls, value: str) -> str:
        try:
            document: Any = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("editor_state must be valid JSON") from exc
        if not isinstance(document, dict) or not isinstance(document.get("root"), dict):
            raise ValueError("editor_state must contain a Lexical root object")
        root = document["root"]
        if (
            root.get("type") != "root"
            or isinstance(root.get("version"), bool)
            or not isinstance(root.get("version"), int)
            or not isinstance(root.get("children"), list)
        ):
            raise ValueError("editor_state must contain a valid Lexical root")
        return value

    @model_validator(mode="after")
    def require_unique_checklist_ids(self) -> Self:
        ids = [item.id for item in self.checklist]
        if len(ids) != len(set(ids)):
            raise ValueError("Checklist item IDs must be unique within a section")
        return self


class ArticleDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[ArticleDraftSection]

    @model_validator(mode="after")
    def require_unique_section_ids(self) -> Self:
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("Draft section IDs must be unique")
        outline_ids = [
            section.outline_section_id
            for section in self.sections
            if section.outline_section_id is not None
        ]
        if len(outline_ids) != len(set(outline_ids)):
            raise ValueError("Outline section links must be unique")
        return self


class ArticleDraftResponse(ArticleDraftUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    article_id: UUID
    created_at: datetime
    updated_at: datetime
