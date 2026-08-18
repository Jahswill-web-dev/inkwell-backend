import asyncio

import pytest
from google.genai import errors, types

from app.schemas.brief import GeneratedBrief
from app.schemas.outline import GeneratedOutline
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefSource,
    VertexGeminiBriefGenerator,
    VertexGeminiOutlineGenerator,
)


class FakeModels:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    async def generate_content(self, **_kwargs: object) -> types.GenerateContentResponse:
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, types.GenerateContentResponse)
        return response


class FakeAsyncClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = FakeAsyncClient(models)


def brief_payload() -> dict[str, object]:
    return {
        "summary": "A focused summary.",
        "core_angle": "A useful angle",
        "audience_insights": ["Readers need practical guidance"],
        "tone_and_style": "Clear and pragmatic",
        "key_takeaways": ["First", "Second", "Third"],
        "evidence_gaps": [],
        "call_to_action": "Try the process",
        "seo": {
            "suggested_titles": ["Title one", "Title two", "Title three"],
            "primary_keyword": "publishing process",
            "secondary_keywords": [],
            "meta_description": "A practical publishing process.",
        },
    }


def generator_without_client() -> VertexGeminiBriefGenerator:
    return object.__new__(VertexGeminiBriefGenerator)


def outline_generator_without_client() -> VertexGeminiOutlineGenerator:
    return object.__new__(VertexGeminiOutlineGenerator)


def brief_source() -> BriefSource:
    return BriefSource(
        working_title="A title",
        notes="Notes",
        target_audience=["Writers"],
        article_goal="inform_and_inspire",
    )


def successful_response() -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_construct(
        candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)],
        parsed=brief_payload(),
    )


def outline_payload() -> dict[str, object]:
    return {
        "sections": [
            {"heading": f"Section {index}", "purpose": "Explain", "key_points": ["Point"]}
            for index in range(3)
        ]
    }


def test_parse_response_returns_validated_content_and_usage() -> None:
    generator = generator_without_client()
    generator.model_id = "test-model"
    response = types.GenerateContentResponse.model_construct(
        candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)],
        parsed=brief_payload(),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=200
        ),
    )

    result = generator._parse_response(response)

    assert isinstance(result.brief, GeneratedBrief)
    assert result.model_id == "test-model"
    assert result.input_token_count == 100
    assert result.output_token_count == 200


def test_parse_outline_response_returns_validated_content_and_usage() -> None:
    generator = outline_generator_without_client()
    generator.model_id = "test-model"
    response = types.GenerateContentResponse.model_construct(
        candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)],
        parsed=outline_payload(),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=50, candidates_token_count=75
        ),
    )

    result = generator._parse_response(response)

    assert isinstance(result.outline, GeneratedOutline)
    assert result.model_id == "test-model"
    assert result.input_token_count == 50
    assert result.output_token_count == 75


def test_parse_response_rejects_blocked_candidate() -> None:
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]
    )

    with pytest.raises(BriefProviderBlockedError):
        generator_without_client()._parse_response(response)


@pytest.mark.parametrize(
    "response",
    [
        types.GenerateContentResponse(candidates=[]),
        types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason=types.FinishReason.MAX_TOKENS)],
            parsed=brief_payload(),
        ),
        types.GenerateContentResponse.model_construct(
            candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)],
            parsed={"summary": "incomplete"},
        ),
    ],
)
def test_parse_response_rejects_incomplete_or_invalid_output(
    response: types.GenerateContentResponse,
) -> None:
    with pytest.raises(BriefProviderResponseError):
        generator_without_client()._parse_response(response)


async def test_generate_retries_one_transient_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.services.ai_service.asyncio.sleep", no_sleep)
    models = FakeModels([errors.APIError(429, {}), successful_response()])
    generator = generator_without_client()
    generator.client = FakeClient(models)  # type: ignore[assignment]
    generator.model_id = "test-model"
    generator.timeout_seconds = 1
    generator.max_output_tokens = 100

    result = await generator.generate(brief_source())

    assert result.brief.summary == "A focused summary."
    assert models.calls == 2


async def test_generate_maps_application_timeout() -> None:
    class SlowModels(FakeModels):
        async def generate_content(self, **_kwargs: object) -> types.GenerateContentResponse:
            await asyncio.sleep(0.02)
            return successful_response()

    generator = generator_without_client()
    generator.client = FakeClient(SlowModels([]))  # type: ignore[assignment]
    generator.model_id = "test-model"
    generator.timeout_seconds = 0.001
    generator.max_output_tokens = 100

    with pytest.raises(BriefProviderTimeoutError):
        await generator.generate(brief_source())
