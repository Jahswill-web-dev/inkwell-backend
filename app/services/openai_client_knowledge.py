from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError

from app.core.config import Settings
from app.prompts.client_knowledge import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.client_knowledge import GeneratedKnowledgeProposals
from app.services.client_knowledge_service import (
    ClientKnowledgeExtractionInput,
    ClientKnowledgeGenerationResult,
    ClientKnowledgeProviderResponseError,
    ClientKnowledgeProviderTimeoutError,
    ClientKnowledgeProviderUnavailableError,
)


class OpenAIClientKnowledgeGenerator:
    def __init__(self, settings: Settings, *, client: AsyncOpenAI | None = None) -> None:
        assert settings.openai_api_key is not None
        self.model_id = settings.openai_knowledge_extraction_model
        self.max_output_tokens = settings.openai_knowledge_extraction_max_output_tokens
        self._client = client or AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_knowledge_extraction_request_timeout_seconds,
        )

    async def generate(
        self, source: ClientKnowledgeExtractionInput
    ) -> ClientKnowledgeGenerationResult:
        try:
            response = await self._client.responses.parse(
                model=self.model_id,
                instructions=SYSTEM_INSTRUCTION,
                input=build_prompt(
                    client_name=source.client_name,
                    client_website=source.client_website,
                    client_industry=source.client_industry,
                    article_title=source.article_title,
                    interviewee_name=source.interviewee_name,
                    turns=source.turns,
                ),
                text_format=GeneratedKnowledgeProposals,
                max_output_tokens=self.max_output_tokens,
                reasoning={"effort": "none"},
                store=False,
            )
        except APITimeoutError as exc:
            raise ClientKnowledgeProviderTimeoutError from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise ClientKnowledgeProviderUnavailableError from exc
        except ValidationError as exc:
            raise ClientKnowledgeProviderResponseError from exc

        parsed = response.output_parsed
        if not isinstance(parsed, GeneratedKnowledgeProposals):
            raise ClientKnowledgeProviderResponseError
        return ClientKnowledgeGenerationResult(
            proposals=parsed,
            model_id=self.model_id,
        )

    async def close(self) -> None:
        await self._client.close()
