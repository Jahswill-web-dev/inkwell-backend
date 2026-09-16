from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.client_interview_questions import GeneratedClientInterviewQuestion
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
    generator._client = SimpleNamespace(
        responses=SimpleNamespace(parse=AsyncMock(side_effect=validation.value))
    )

    with pytest.raises(
        BriefProviderResponseError,
        match="A question cannot contain a line break",
    ):
        await generator.generate_client_questions({})
