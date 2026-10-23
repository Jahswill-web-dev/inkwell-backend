from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.client_interview_questions import (
    GeneratedClientInterviewQuestion,
    GeneratedClientInterviewQuestions,
)
from app.services.ai_service import BriefProviderResponseError
from app.services.openai_interview_questions import OpenAIClientInterviewQuestionGenerator


def test_accepts_spoken_interview_prompt_without_question_mark() -> None:
    question = GeneratedClientInterviewQuestion.model_validate(
        {"question": "Tell me about the toughest customer objection you faced"}
    )

    assert question.question == "Tell me about the toughest customer objection you faced"


def test_rejects_multiline_interview_prompt() -> None:
    with pytest.raises(ValidationError, match="Question must not contain line breaks"):
        GeneratedClientInterviewQuestion.model_validate(
            {"question": "Tell me about the customer\nchallenge you solved"}
        )


async def test_reports_a_clear_error_for_malformed_ai_question_output() -> None:
    with pytest.raises(ValidationError) as validation:
        GeneratedClientInterviewQuestion.model_validate(
            {"question": "Tell me about the customer\nchallenge you solved"}
        )

    generator = object.__new__(OpenAIClientInterviewQuestionGenerator)
    generator._model = "test-model"
    generator._reasoning_effort = "medium"
    generator._client = SimpleNamespace(
        responses=SimpleNamespace(parse=AsyncMock(side_effect=validation.value))
    )

    with pytest.raises(
        BriefProviderResponseError,
        match="A question cannot contain a line break",
    ):
        await generator.generate_client_questions({})


async def test_question_generation_uses_medium_reasoning() -> None:
    parsed = GeneratedClientInterviewQuestions(
        questions=[
            GeneratedClientInterviewQuestion(question=f"Tell me about example {index}")
            for index in range(5)
        ]
    )
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed, usage=None))
    generator = object.__new__(OpenAIClientInterviewQuestionGenerator)
    generator._model = "gpt-5.6-sol"
    generator._reasoning_effort = "medium"
    generator._client = SimpleNamespace(responses=SimpleNamespace(parse=parse))

    result = await generator.generate_client_questions({"topic": "research"})

    assert result.model_id == "gpt-5.6-sol"
    assert parse.await_args.kwargs["reasoning"] == {"effort": "medium"}
