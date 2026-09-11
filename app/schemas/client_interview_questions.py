from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=180),
]


class GeneratedClientInterviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: QuestionText

    @field_validator("question")
    @classmethod
    def require_single_question(cls, value: str) -> str:
        if "\n" in value or "\r" in value or not value.endswith("?"):
            raise ValueError("Question must be one sentence ending in a question mark")
        return value


class GeneratedClientInterviewQuestions(BaseModel):
    questions: Annotated[
        list[GeneratedClientInterviewQuestion], Field(min_length=5, max_length=7)
    ]

    @field_validator("questions")
    @classmethod
    def require_unique_questions(
        cls, value: list[GeneratedClientInterviewQuestion]
    ) -> list[GeneratedClientInterviewQuestion]:
        normalized = [item.question.casefold() for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Interview questions must be unique")
        return value
