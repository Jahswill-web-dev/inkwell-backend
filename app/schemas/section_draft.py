from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.section_interview import SectionContentBlock


class SectionDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
        | None
    ) = None


class SectionDraftResponse(BaseModel):
    section_id: UUID
    blocks: list[SectionContentBlock]
