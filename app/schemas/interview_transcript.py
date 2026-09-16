from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=200)
    speaker: Literal["participant", "interviewer"]
    text: str = Field(min_length=1, max_length=10_000)


class TranscriptTurnBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: list[TranscriptTurn] = Field(min_length=1, max_length=40)


class InterviewInsightItem(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    source_item_ids: list[str] = Field(min_length=1, max_length=8)


class GeneratedInterviewInsights(BaseModel):
    summary: str = Field(min_length=1, max_length=4_000)
    key_insights: list[InterviewInsightItem] = Field(max_length=12)
    examples_and_evidence: list[InterviewInsightItem] = Field(max_length=10)
    claims_to_verify: list[InterviewInsightItem] = Field(max_length=10)
    open_questions: list[str] = Field(max_length=10)


class InterviewTranscriptResponse(BaseModel):
    id: UUID
    invitation_id: UUID
    turns: list[TranscriptTurn]
    insight_status: Literal["pending", "ready", "failed"]
    insights: GeneratedInterviewInsights | None
    model_id: str | None
    generation_error: str | None
    created_at: datetime
    updated_at: datetime
