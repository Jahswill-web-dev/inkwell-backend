from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import SecretStr

from app.core.config import Settings
from app.prompts.client_knowledge import SYSTEM_INSTRUCTION
from app.schemas.client_knowledge import GeneratedKnowledgeProposals
from app.schemas.interview_transcript import TranscriptTurn
from app.services.client_knowledge_service import (
    ClientKnowledgeExtractionInput,
    ClientKnowledgeProviderResponseError,
    ClientKnowledgeProviderTimeoutError,
    ClientKnowledgeProviderUnavailableError,
)
from app.services.openai_client_knowledge import OpenAIClientKnowledgeGenerator


def _source() -> ClientKnowledgeExtractionInput:
    return ClientKnowledgeExtractionInput(
        client_name="Northstar Labs",
        client_website="https://northstar.example",
        client_industry="Software",
        article_title="How research teams work",
        interviewee_name="Avery Chen",
        turns=[
            TranscriptTurn(
                item_id="answer-1",
                speaker="participant",
                text="Evidence mapping reduces review time.",
            )
        ],
    )


def _proposals() -> GeneratedKnowledgeProposals:
    return GeneratedKnowledgeProposals.model_validate(
        {
            "proposals": [
                {
                    "category": "processes_and_frameworks",
                    "statement": "Northstar uses evidence mapping.",
                    "knowledge_type": "fact",
                    "source_item_ids": ["answer-1"],
                }
            ]
        }
    )


def _generator(
    settings: Settings,
    *,
    response: object | None = None,
    error: Exception | None = None,
) -> tuple[OpenAIClientKnowledgeGenerator, SimpleNamespace]:
    parse = AsyncMock(return_value=response, side_effect=error)
    client = SimpleNamespace(
        responses=SimpleNamespace(parse=parse),
        close=AsyncMock(),
    )
    configured = settings.model_copy(
        update={
            "openai_api_key": SecretStr("test-openai-key"),
            "openai_knowledge_extraction_model": "knowledge-test-model",
            "openai_knowledge_extraction_max_output_tokens": 2_500,
        }
    )
    return (
        OpenAIClientKnowledgeGenerator(configured, client=cast(AsyncOpenAI, client)),
        client,
    )


async def test_adapter_returns_validated_proposals_and_required_controls(
    settings: Settings,
) -> None:
    generator, client = _generator(
        settings,
        response=SimpleNamespace(output_parsed=_proposals()),
    )

    result = await generator.generate(_source())

    assert result.proposals.proposals[0].category == "processes_and_frameworks"
    assert result.model_id == "knowledge-test-model"
    request = client.responses.parse.await_args.kwargs
    assert request["model"] == "knowledge-test-model"
    assert request["instructions"] == SYSTEM_INSTRUCTION
    assert request["text_format"] is GeneratedKnowledgeProposals
    assert request["max_output_tokens"] == 2_500
    assert request["reasoning"] == {"effort": "none"}
    assert request["store"] is False
    assert "answer-1" in request["input"]


async def test_adapter_rejects_an_unparsed_response(settings: Settings) -> None:
    generator, _ = _generator(
        settings,
        response=SimpleNamespace(output_parsed=None),
    )

    with pytest.raises(ClientKnowledgeProviderResponseError):
        await generator.generate(_source())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/responses")),
            ClientKnowledgeProviderTimeoutError,
        ),
        (
            APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com/v1/responses")
            ),
            ClientKnowledgeProviderUnavailableError,
        ),
    ],
)
async def test_adapter_maps_provider_failures(
    settings: Settings,
    error: Exception,
    expected: type[Exception],
) -> None:
    generator, _ = _generator(settings, error=error)

    with pytest.raises(expected):
        await generator.generate(_source())


async def test_adapter_closes_its_client(settings: Settings) -> None:
    generator, client = _generator(settings, response=SimpleNamespace(output_parsed=_proposals()))

    await generator.close()

    client.close.assert_awaited_once()
