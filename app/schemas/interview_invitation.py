from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

ParticipantName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
InvitationStatus = Literal["active", "revoked"]
ProgressState = Literal["not_opened", "opened", "in_progress", "completed"]


class InterviewInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_name: ParticipantName
    participant_email: EmailStr
    expires_on: str | None = None


class InterviewInvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    article_id: UUID
    article_title: str | None = None
    client_name: str | None = None
    writer_name: str | None = None
    participant_name: str
    participant_email: str
    token: str
    status: InvitationStatus
    expires_at: datetime | None
    progress_state: ProgressState
    questions_answered: int
    estimated_questions: int
    opened_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GuestQuestion(BaseModel):
    id: str
    text: str
    kind: Literal["core", "follow_up", "final_detail"] = "core"


class GuestAnswer(BaseModel):
    question_id: str
    question: str
    answer: str
    answered_at: str


class GuestSessionData(BaseModel):
    token: str
    state: Literal["welcome", "active", "paused", "completed"] = "welcome"
    questions: list[GuestQuestion] = Field(default_factory=list)
    current_question_index: int = 0
    answers: list[GuestAnswer] = Field(default_factory=list)
    completion_reason: Literal["sufficient", "participant_finished", "question_limit"] | None = None
    final_detail_added: bool = False
    draft_answer: str = ""
    updated_at: str | None = None


class GuestInterviewResponse(BaseModel):
    invitation: InterviewInvitationResponse
    session: GuestSessionData


class GuestSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["welcome", "active", "paused", "completed"] | None = None
    questions: list[dict[str, Any]] | None = None
    current_question_index: int | None = None
    answers: list[dict[str, Any]] | None = None
    completion_reason: Literal["sufficient", "participant_finished", "question_limit"] | None = None
    final_detail_added: bool | None = None
    draft_answer: str | None = None
