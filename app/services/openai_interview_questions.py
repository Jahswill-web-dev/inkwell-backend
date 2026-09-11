from __future__ import annotations

from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.core.config import Settings
from app.prompts.client_interview_questions import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.client_interview_questions import GeneratedClientInterviewQuestions
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
)
from app.services.section_interview_ai import ClientInterviewQuestionsResult


class OpenAIClientInterviewQuestionGenerator:
    def __init__(self, settings: Settings) -> None:
        assert settings.openai_api_key is not None
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_realtime_request_timeout_seconds,
        )
        self._model = settings.openai_interview_question_model

    async def generate_client_questions(
        self, context: dict[str, Any]
    ) -> ClientInterviewQuestionsResult:
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=SYSTEM_INSTRUCTION,
                input=build_prompt(context),
                text_format=GeneratedClientInterviewQuestions,
                max_output_tokens=900,
                reasoning={"effort": "none"},
                store=False,
            )
        except APITimeoutError as exc:
            raise BriefProviderTimeoutError from exc
        except APIConnectionError as exc:
            raise BriefProviderUnavailableError from exc
        except APIStatusError as exc:
            if exc.status_code in {400, 422}:
                raise BriefProviderBlockedError from exc
            raise BriefProviderUnavailableError from exc

        parsed = response.output_parsed
        if not isinstance(parsed, GeneratedClientInterviewQuestions):
            raise BriefProviderResponseError
        return ClientInterviewQuestionsResult(
            questions=parsed,
            model_id=self._model,
            input_token_count=response.usage.input_tokens if response.usage else None,
            output_token_count=response.usage.output_tokens if response.usage else None,
        )

    async def close(self) -> None:
        await self._client.close()
