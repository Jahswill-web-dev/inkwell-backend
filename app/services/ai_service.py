from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.auth.exceptions import GoogleAuthError
from google.genai import errors, types
from pydantic import ValidationError

from app.core.config import Settings
from app.prompts.brief import SYSTEM_INSTRUCTION, build_brief_prompt
from app.prompts.outline import SYSTEM_INSTRUCTION as OUTLINE_SYSTEM_INSTRUCTION
from app.prompts.outline import (
    build_outline_prompt,
)
from app.schemas.brief import GeneratedBrief
from app.schemas.outline import GeneratedOutline

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
BLOCKED_FINISH_REASONS = {
    types.FinishReason.SAFETY,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.SPII,
}


@dataclass(frozen=True)
class BriefSource:
    working_title: str
    notes: str
    target_audience: list[str]
    article_goal: str

    def as_prompt_data(self) -> dict[str, object]:
        return {
            "working_title": self.working_title,
            "notes": self.notes,
            "target_audience": self.target_audience,
            "article_goal": self.article_goal,
        }


@dataclass(frozen=True)
class BriefGenerationResult:
    brief: GeneratedBrief
    model_id: str
    input_token_count: int | None
    output_token_count: int | None


class BriefGenerator(Protocol):
    async def generate(self, source: BriefSource) -> BriefGenerationResult: ...


@dataclass(frozen=True)
class OutlineSource:
    summary: str
    core_angle: str
    audience_insights: list[str]
    tone_and_style: str
    key_takeaways: list[str]
    evidence_gaps: list[str]
    call_to_action: str

    def as_prompt_data(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "core_angle": self.core_angle,
            "audience_insights": self.audience_insights,
            "tone_and_style": self.tone_and_style,
            "key_takeaways": self.key_takeaways,
            "evidence_gaps": self.evidence_gaps,
            "call_to_action": self.call_to_action,
        }


@dataclass(frozen=True)
class OutlineGenerationResult:
    outline: GeneratedOutline
    model_id: str
    input_token_count: int | None
    output_token_count: int | None


class OutlineGenerator(Protocol):
    async def generate(self, source: OutlineSource) -> OutlineGenerationResult: ...


class BriefProviderUnavailableError(Exception):
    pass


class BriefProviderTimeoutError(Exception):
    pass


class BriefProviderBlockedError(Exception):
    pass


class BriefProviderResponseError(Exception):
    pass


class VertexGeminiBriefGenerator:
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

    async def generate(self, source: BriefSource) -> BriefGenerationResult:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._generate_with_retry(source)
        except TimeoutError as exc:
            raise BriefProviderTimeoutError from exc

    async def _generate_with_retry(self, source: BriefSource) -> BriefGenerationResult:
        for attempt in range(2):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model_id,
                    contents=build_brief_prompt(source.as_prompt_data()),
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        temperature=0.3,
                        max_output_tokens=self.max_output_tokens,
                        response_mime_type="application/json",
                        response_schema=GeneratedBrief,
                    ),
                )
                return self._parse_response(response)
            except errors.APIError as exc:
                if exc.code in TRANSIENT_STATUS_CODES and attempt == 0:
                    await asyncio.sleep(0.25 + random.uniform(0, 0.25))
                    continue
                raise BriefProviderUnavailableError from exc
            except GoogleAuthError as exc:
                raise BriefProviderUnavailableError from exc
        raise BriefProviderUnavailableError

    def _parse_response(self, response: types.GenerateContentResponse) -> BriefGenerationResult:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise BriefProviderBlockedError

        candidate = response.candidates[0] if response.candidates else None
        if candidate and candidate.finish_reason in BLOCKED_FINISH_REASONS:
            raise BriefProviderBlockedError
        if candidate is None or candidate.finish_reason != types.FinishReason.STOP:
            raise BriefProviderResponseError

        try:
            parsed = response.parsed
            if isinstance(parsed, GeneratedBrief):
                brief = parsed
            else:
                brief = GeneratedBrief.model_validate(parsed)
        except ValidationError as exc:
            raise BriefProviderResponseError from exc

        usage = response.usage_metadata
        return BriefGenerationResult(
            brief=brief,
            model_id=self.model_id,
            input_token_count=usage.prompt_token_count if usage else None,
            output_token_count=usage.candidates_token_count if usage else None,
        )

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()


class VertexGeminiOutlineGenerator:
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

    async def generate(self, source: OutlineSource) -> OutlineGenerationResult:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._generate_with_retry(source)
        except TimeoutError as exc:
            raise BriefProviderTimeoutError from exc

    async def _generate_with_retry(self, source: OutlineSource) -> OutlineGenerationResult:
        for attempt in range(2):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model_id,
                    contents=build_outline_prompt(source.as_prompt_data()),
                    config=types.GenerateContentConfig(
                        system_instruction=OUTLINE_SYSTEM_INSTRUCTION,
                        temperature=0.3,
                        max_output_tokens=self.max_output_tokens,
                        response_mime_type="application/json",
                        response_schema=GeneratedOutline,
                    ),
                )
                return self._parse_response(response)
            except errors.APIError as exc:
                if exc.code in TRANSIENT_STATUS_CODES and attempt == 0:
                    await asyncio.sleep(0.25 + random.uniform(0, 0.25))
                    continue
                raise BriefProviderUnavailableError from exc
            except GoogleAuthError as exc:
                raise BriefProviderUnavailableError from exc
        raise BriefProviderUnavailableError

    def _parse_response(self, response: types.GenerateContentResponse) -> OutlineGenerationResult:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise BriefProviderBlockedError

        candidate = response.candidates[0] if response.candidates else None
        if candidate and candidate.finish_reason in BLOCKED_FINISH_REASONS:
            raise BriefProviderBlockedError
        if candidate is None or candidate.finish_reason != types.FinishReason.STOP:
            raise BriefProviderResponseError

        try:
            parsed = response.parsed
            if isinstance(parsed, GeneratedOutline):
                outline = parsed
            else:
                outline = GeneratedOutline.model_validate(parsed)
        except ValidationError as exc:
            raise BriefProviderResponseError from exc

        usage = response.usage_metadata
        return OutlineGenerationResult(
            outline=outline,
            model_id=self.model_id,
            input_token_count=usage.prompt_token_count if usage else None,
            output_token_count=usage.candidates_token_count if usage else None,
        )

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()
