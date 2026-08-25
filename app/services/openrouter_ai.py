from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.prompts.brief import SYSTEM_INSTRUCTION as BRIEF_SYSTEM_INSTRUCTION
from app.prompts.brief import build_brief_prompt
from app.prompts.outline import SYSTEM_INSTRUCTION as OUTLINE_SYSTEM_INSTRUCTION
from app.prompts.outline import build_outline_prompt
from app.prompts.section_interview import (
    DRAFT_SYSTEM_INSTRUCTION,
    QUESTIONS_SYSTEM_INSTRUCTION,
    build_draft_prompt,
    build_questions_prompt,
)
from app.prompts.talking_points import SYSTEM_INSTRUCTION as TALKING_POINTS_SYSTEM_INSTRUCTION
from app.prompts.talking_points import build_talking_points_prompt
from app.schemas.brief import GeneratedBrief
from app.schemas.outline import GeneratedOutline
from app.schemas.section_interview import GeneratedSectionDraft, GeneratedSectionQuestions
from app.schemas.talking_points import GeneratedTalkingPoints
from app.services.ai_service import (
    BriefGenerationResult,
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    BriefSource,
    OutlineGenerationResult,
    OutlineSource,
    TalkingPointsGenerationResult,
    TalkingPointsSource,
)
from app.services.section_interview_ai import SectionDraftResult, SectionQuestionsResult

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
BLOCKED_TERMS = ("moderation", "content policy", "safety", "blocked", "prohibited")


@dataclass(frozen=True)
class OpenRouterGenerationResult[SchemaT: BaseModel]:
    content: SchemaT
    model_id: str
    input_token_count: int | None
    output_token_count: int | None


class OpenRouterJSONClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        assert settings.openrouter_api_key is not None
        self.model_id = settings.openrouter_model_id
        self.max_output_tokens = settings.openrouter_max_output_tokens
        self.data_collection = settings.openrouter_data_collection
        self.allow_fallbacks = settings.openrouter_allow_fallbacks
        self.url = f"{settings.openrouter_base_url}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.openrouter_request_timeout_seconds),
        )

    async def generate(
        self,
        *,
        system_instruction: str,
        user_prompt: str,
        schema: type[SchemaT],
    ) -> OpenRouterGenerationResult[SchemaT]:
        schema_json = json.dumps(
            schema.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        system_with_schema = (
            f"{system_instruction}\n\n"
            "Return exactly one JSON object matching OUTPUT_SCHEMA. Do not add Markdown fences, "
            "commentary, or fields outside the schema.\n\n"
            f"<OUTPUT_SCHEMA>\n{schema_json}\n</OUTPUT_SCHEMA>"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_with_schema},
            {"role": "user", "content": user_prompt},
        ]
        body = await self._request(messages)
        response_bodies = [body]
        try:
            parsed = self._parse(body, schema)
        except (ValidationError, ValueError, TypeError) as exc:
            invalid_content = self._content_for_repair(body)
            repair_messages = [
                *messages,
                {"role": "assistant", "content": invalid_content},
                {
                    "role": "user",
                    "content": (
                        "Repair the previous response so it matches OUTPUT_SCHEMA exactly. Return "
                        "only the corrected JSON object. Validation errors:\n"
                        f"{_validation_details(exc)}"
                    ),
                },
            ]
            repaired_body = await self._request(repair_messages)
            try:
                parsed = self._parse(repaired_body, schema)
            except (ValidationError, ValueError, TypeError) as repair_exc:
                raise BriefProviderResponseError from repair_exc
            body = repaired_body
            response_bodies.append(repaired_body)

        resolved_model = body.get("model")
        model_id = resolved_model if isinstance(resolved_model, str) else self.model_id
        result = OpenRouterGenerationResult(
            content=parsed,
            model_id=model_id,
            input_token_count=_total_usage(response_bodies, "prompt_tokens"),
            output_token_count=_total_usage(response_bodies, "completion_tokens"),
        )
        logger.info(
            "AI generation completed provider=openrouter model_id=%s input_tokens=%s "
            "output_tokens=%s",
            result.model_id,
            result.input_token_count,
            result.output_token_count,
        )
        return result

    async def _request(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": self.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "provider": {
                "require_parameters": True,
                "data_collection": self.data_collection,
                "allow_fallbacks": self.allow_fallbacks,
            },
        }
        for attempt in range(2):
            try:
                response = await self._http.post(self.url, headers=self._headers, json=payload)
            except httpx.TimeoutException as exc:
                raise BriefProviderTimeoutError from exc
            except httpx.HTTPError as exc:
                raise BriefProviderUnavailableError from exc

            if response.status_code in TRANSIENT_STATUS_CODES and attempt == 0:
                await asyncio.sleep(0.25 + random.uniform(0, 0.25))
                continue
            if response.status_code >= 400:
                self._raise_http_error(response)
            try:
                body = response.json()
            except ValueError as exc:
                raise BriefProviderResponseError from exc
            if not isinstance(body, dict):
                raise BriefProviderResponseError
            return body
        raise BriefProviderUnavailableError

    def _parse(self, body: dict[str, Any], schema: type[SchemaT]) -> SchemaT:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("OpenRouter response did not contain a choice")
        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            raise BriefProviderBlockedError
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenRouter response did not contain a message")
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            raise BriefProviderBlockedError
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenRouter response content was empty")
        return schema.model_validate_json(content)

    def _content_for_repair(self, body: dict[str, Any]) -> str:
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return str(message["content"])
        return "{}"

    def _raise_http_error(self, response: httpx.Response) -> None:
        message = response.text.casefold()
        if response.status_code == 403 and any(term in message for term in BLOCKED_TERMS):
            raise BriefProviderBlockedError
        raise BriefProviderUnavailableError

    async def close(self) -> None:
        await self._http.aclose()


class OpenRouterBriefGenerator:
    def __init__(self, client: OpenRouterJSONClient) -> None:
        self.client = client

    async def generate(self, source: BriefSource) -> BriefGenerationResult:
        result = await self.client.generate(
            system_instruction=BRIEF_SYSTEM_INSTRUCTION,
            user_prompt=build_brief_prompt(source.as_prompt_data()),
            schema=GeneratedBrief,
        )
        return BriefGenerationResult(
            brief=result.content,
            model_id=result.model_id,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
        )


class OpenRouterOutlineGenerator:
    def __init__(self, client: OpenRouterJSONClient) -> None:
        self.client = client

    async def generate(self, source: OutlineSource) -> OutlineGenerationResult:
        result = await self.client.generate(
            system_instruction=OUTLINE_SYSTEM_INSTRUCTION,
            user_prompt=build_outline_prompt(source.as_prompt_data()),
            schema=GeneratedOutline,
        )
        return OutlineGenerationResult(
            outline=result.content,
            model_id=result.model_id,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
        )


class OpenRouterTalkingPointsGenerator:
    def __init__(self, client: OpenRouterJSONClient) -> None:
        self.client = client

    async def generate(self, source: TalkingPointsSource) -> TalkingPointsGenerationResult:
        result = await self.client.generate(
            system_instruction=TALKING_POINTS_SYSTEM_INSTRUCTION,
            user_prompt=build_talking_points_prompt(source.context, source.instruction),
            schema=GeneratedTalkingPoints,
        )
        return TalkingPointsGenerationResult(
            talking_points=result.content,
            model_id=result.model_id,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
        )


class OpenRouterSectionInterviewGenerator:
    def __init__(self, client: OpenRouterJSONClient) -> None:
        self.client = client

    async def generate_questions(
        self, context: dict[str, Any], instruction: str | None
    ) -> SectionQuestionsResult:
        result = await self.client.generate(
            system_instruction=QUESTIONS_SYSTEM_INSTRUCTION,
            user_prompt=build_questions_prompt(context, instruction),
            schema=GeneratedSectionQuestions,
        )
        return SectionQuestionsResult(
            questions=result.content,
            model_id=result.model_id,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
        )

    async def generate_draft(
        self, context: dict[str, Any], questions_and_answers: list[dict[str, Any]]
    ) -> SectionDraftResult:
        result = await self.client.generate(
            system_instruction=DRAFT_SYSTEM_INSTRUCTION,
            user_prompt=build_draft_prompt(context, questions_and_answers),
            schema=GeneratedSectionDraft,
        )
        return SectionDraftResult(
            draft=result.content,
            model_id=result.model_id,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
        )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _total_usage(bodies: list[dict[str, Any]], field: str) -> int | None:
    counts: list[int] = []
    for body in bodies:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            continue
        count = _optional_int(usage.get(field))
        if count is not None:
            counts.append(count)
    return sum(counts) if counts else None


def _validation_details(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        details: object = exc.errors(include_url=False, include_input=False)
    else:
        details = str(exc)
    return json.dumps(details, ensure_ascii=False)[:4000]
