from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Protocol

from google import genai
from google.auth.exceptions import GoogleAuthError
from google.genai import errors, types
from pydantic import ValidationError

from app.core.config import Settings
from app.prompts.section_draft import (
    SYSTEM_INSTRUCTION as DIRECT_DRAFT_SYSTEM_INSTRUCTION,
)
from app.prompts.section_draft import build_section_draft_prompt
from app.prompts.section_interview import (
    DRAFT_SYSTEM_INSTRUCTION,
    QUESTIONS_SYSTEM_INSTRUCTION,
    build_draft_prompt,
    build_questions_prompt,
)
from app.schemas.section_interview import GeneratedSectionDraft, GeneratedSectionQuestions
from app.services.ai_service import (
    BLOCKED_FINISH_REASONS,
    TRANSIENT_STATUS_CODES,
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
)


@dataclass(frozen=True)
class SectionQuestionsResult:
    questions: GeneratedSectionQuestions
    model_id: str
    input_token_count: int | None
    output_token_count: int | None


@dataclass(frozen=True)
class SectionDraftResult:
    draft: GeneratedSectionDraft
    model_id: str
    input_token_count: int | None
    output_token_count: int | None


class SectionInterviewGenerator(Protocol):
    async def generate_questions(
        self, context: dict[str, Any], instruction: str | None
    ) -> SectionQuestionsResult: ...

    async def generate_draft(
        self, context: dict[str, Any], questions_and_answers: list[dict[str, Any]]
    ) -> SectionDraftResult: ...

    async def generate_direct_draft(
        self, context: dict[str, Any], instruction: str | None
    ) -> SectionDraftResult: ...


class VertexGeminiSectionInterviewGenerator:
    def __init__(self, settings: Settings) -> None:
        assert settings.vertex_project_id is not None
        self.model_id = settings.vertex_model_id
        self.timeout_seconds = settings.vertex_request_timeout_seconds
        self.max_output_tokens = settings.vertex_max_output_tokens
        self.client = genai.Client(
            vertexai=True,
            project=settings.vertex_project_id,
            location=settings.vertex_location,
            http_options=types.HttpOptions(api_version="v1"),
        )

    async def generate_questions(
        self, context: dict[str, Any], instruction: str | None
    ) -> SectionQuestionsResult:
        response = await self._generate(
            contents=build_questions_prompt(context, instruction),
            system_instruction=QUESTIONS_SYSTEM_INSTRUCTION,
            schema=GeneratedSectionQuestions,
        )
        return SectionQuestionsResult(
            questions=self._parse(response, GeneratedSectionQuestions),
            model_id=self.model_id,
            input_token_count=_input_tokens(response),
            output_token_count=_output_tokens(response),
        )

    async def generate_draft(
        self, context: dict[str, Any], questions_and_answers: list[dict[str, Any]]
    ) -> SectionDraftResult:
        response = await self._generate(
            contents=build_draft_prompt(context, questions_and_answers),
            system_instruction=DRAFT_SYSTEM_INSTRUCTION,
            schema=GeneratedSectionDraft,
        )
        return SectionDraftResult(
            draft=self._parse(response, GeneratedSectionDraft),
            model_id=self.model_id,
            input_token_count=_input_tokens(response),
            output_token_count=_output_tokens(response),
        )

    async def generate_direct_draft(
        self, context: dict[str, Any], instruction: str | None
    ) -> SectionDraftResult:
        response = await self._generate(
            contents=build_section_draft_prompt(context, instruction),
            system_instruction=DIRECT_DRAFT_SYSTEM_INSTRUCTION,
            schema=GeneratedSectionDraft,
        )
        return SectionDraftResult(
            draft=self._parse(response, GeneratedSectionDraft),
            model_id=self.model_id,
            input_token_count=_input_tokens(response),
            output_token_count=_output_tokens(response),
        )

    async def _generate(
        self, *, contents: str, system_instruction: str, schema: type[Any]
    ) -> types.GenerateContentResponse:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                for attempt in range(2):
                    try:
                        return await self.client.aio.models.generate_content(
                            model=self.model_id,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.3,
                                max_output_tokens=self.max_output_tokens,
                                response_mime_type="application/json",
                                response_schema=schema,
                            ),
                        )
                    except errors.APIError as exc:
                        if exc.code in TRANSIENT_STATUS_CODES and attempt == 0:
                            await asyncio.sleep(0.25 + random.uniform(0, 0.25))
                            continue
                        raise BriefProviderUnavailableError from exc
                    except GoogleAuthError as exc:
                        raise BriefProviderUnavailableError from exc
        except TimeoutError as exc:
            raise BriefProviderTimeoutError from exc
        raise BriefProviderUnavailableError

    def _parse[SchemaT](
        self, response: types.GenerateContentResponse, schema: type[SchemaT]
    ) -> SchemaT:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise BriefProviderBlockedError
        candidate = response.candidates[0] if response.candidates else None
        if candidate and candidate.finish_reason in BLOCKED_FINISH_REASONS:
            raise BriefProviderBlockedError
        if candidate is None or candidate.finish_reason != types.FinishReason.STOP:
            raise BriefProviderResponseError
        try:
            parsed = response.parsed
            return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)  # type: ignore[attr-defined]
        except ValidationError as exc:
            raise BriefProviderResponseError from exc

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()


def _input_tokens(response: types.GenerateContentResponse) -> int | None:
    return response.usage_metadata.prompt_token_count if response.usage_metadata else None


def _output_tokens(response: types.GenerateContentResponse) -> int | None:
    return response.usage_metadata.candidates_token_count if response.usage_metadata else None
