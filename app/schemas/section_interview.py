from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
GeneratedQuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=120),
]
GeneratedAnswerGuidance = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]


class SectionInterviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
        | None
    ) = None


class GeneratedSectionQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_piece: NonEmptyText
    question: GeneratedQuestionText
    answer_guidance: GeneratedAnswerGuidance | None = None

    @field_validator("question")
    @classmethod
    def require_one_question_sentence(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Question must not contain line breaks")
        if not value.endswith("?") or value.count("?") != 1:
            raise ValueError("Question must end with exactly one question mark")
        if any(mark in value[:-1] for mark in ".!"):
            raise ValueError("Question must contain only one sentence")
        return value


class GeneratedSectionQuestions(BaseModel):
    questions: Annotated[list[GeneratedSectionQuestion], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def require_unique_questions(self) -> GeneratedSectionQuestions:
        normalized = [item.question.casefold() for item in self.questions]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Section interview questions must be unique")
        return self


class SectionQuestion(BaseModel):
    """Persisted response shape kept relaxed so legacy interviews remain readable."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    missing_piece: NonEmptyText
    question: NonEmptyText
    answer_guidance: NonEmptyText | None = None


class SectionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    answer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10_000)] | None


class SectionAnswersUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: Annotated[list[SectionAnswer], Field(max_length=4)]

    @model_validator(mode="after")
    def require_unique_question_ids(self) -> SectionAnswersUpdate:
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Answer question IDs must be unique")
        return self


class ParagraphBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"]
    text: NonEmptyText


class SubheadingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["subheading"]
    text: NonEmptyText


class ListBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bulleted_list", "numbered_list"]
    items: Annotated[list[NonEmptyText], Field(min_length=1)]


SectionContentBlock = ParagraphBlock | SubheadingBlock | ListBlock


class GeneratedSectionDraft(BaseModel):
    blocks: Annotated[list[SectionContentBlock], Field(min_length=1)]


class SectionInterviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    draft_id: UUID
    section_id: UUID
    status: Literal["awaiting_answers", "generated"]
    questions: list[SectionQuestion]
    answers: list[SectionAnswer]
    generated_blocks: list[SectionContentBlock] | None
    is_stale: bool
    created_at: datetime
    updated_at: datetime
