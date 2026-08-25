from __future__ import annotations

import json
from collections.abc import Callable, Sequence

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.schemas.brief import GeneratedBrief
from app.schemas.outline import GeneratedOutline
from app.schemas.section_interview import GeneratedSectionDraft, GeneratedSectionQuestions
from app.schemas.talking_points import GeneratedTalkingPoints
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    BriefSource,
    OutlineSource,
    TalkingPointsSource,
)
from app.services.openrouter_ai import (
    OpenRouterBriefGenerator,
    OpenRouterJSONClient,
    OpenRouterOutlineGenerator,
    OpenRouterSectionInterviewGenerator,
    OpenRouterTalkingPointsGenerator,
)


class ResponseQueue:
    def __init__(self, responses: Sequence[httpx.Response | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response.request = request
        return response


def openrouter_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "ai_provider": "openrouter",
            "openrouter_api_key": SecretStr("test-openrouter-key"),
            "openrouter_model_id": "deepseek/deepseek-v4-pro-0813",
        }
    )


def completion(content: object, *, model: object = "resolved-model") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": content if isinstance(content, str) else json.dumps(content),
                    },
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        },
    )


def make_client(
    settings: Settings, responses: Sequence[httpx.Response | Exception]
) -> tuple[OpenRouterJSONClient, ResponseQueue]:
    queue = ResponseQueue(responses)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(queue))
    return OpenRouterJSONClient(openrouter_settings(settings), http_client=http_client), queue


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


def outline_payload() -> dict[str, object]:
    return {
        "sections": [
            {"heading": f"Section {index}", "purpose": "Explain", "key_points": ["Point"]}
            for index in range(3)
        ]
    }


async def test_all_openrouter_adapters_validate_outputs_and_send_required_controls(
    settings: Settings,
) -> None:
    client, queue = make_client(
        settings,
        [
            completion(brief_payload()),
            completion(outline_payload()),
            completion({"points": ["First", "Second", "Third"]}),
            completion(
                {
                    "questions": [
                        {"missing_piece": "Example", "question": "What happened?"},
                        {"missing_piece": "Lesson", "question": "What changed?"},
                    ]
                }
            ),
            completion({"blocks": [{"type": "paragraph", "text": "A complete section."}]}),
        ],
    )
    try:
        brief = await OpenRouterBriefGenerator(client).generate(
            BriefSource(
                working_title="Title",
                notes="Notes",
                target_audience=["Writers"],
                article_goal="inform_and_inspire",
            )
        )
        outline = await OpenRouterOutlineGenerator(client).generate(
            OutlineSource(
                summary="Summary",
                core_angle="Angle",
                audience_insights=["Insight"],
                tone_and_style="Clear",
                key_takeaways=["One", "Two", "Three"],
                evidence_gaps=[],
                call_to_action="Act",
            )
        )
        points = await OpenRouterTalkingPointsGenerator(client).generate(
            TalkingPointsSource(context={"selected_section": {}}, instruction=None)
        )
        interview_generator = OpenRouterSectionInterviewGenerator(client)
        questions = await interview_generator.generate_questions(
            {"selected_section": {"goal": "Explain"}}, None
        )
        draft = await interview_generator.generate_draft(
            {"selected_section": {"goal": "Explain"}},
            [{"question": "What happened?", "answer": "Something useful."}],
        )
    finally:
        await client.close()

    assert isinstance(brief.brief, GeneratedBrief)
    assert isinstance(outline.outline, GeneratedOutline)
    assert isinstance(points.talking_points, GeneratedTalkingPoints)
    assert isinstance(questions.questions, GeneratedSectionQuestions)
    assert isinstance(draft.draft, GeneratedSectionDraft)
    assert brief.model_id == "resolved-model"
    assert brief.input_token_count == 12
    assert brief.output_token_count == 8
    assert len(queue.requests) == 5

    first_request = queue.requests[0]
    assert str(first_request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert first_request.headers["Authorization"] == "Bearer test-openrouter-key"
    body = json.loads(first_request.content)
    assert body["model"] == "deepseek/deepseek-v4-pro-0813"
    assert body["response_format"] == {"type": "json_object"}
    assert body["provider"] == {
        "require_parameters": True,
        "data_collection": "deny",
        "allow_fallbacks": True,
    }
    assert body["messages"][0]["role"] == "system"
    assert "<OUTPUT_SCHEMA>" in body["messages"][0]["content"]
    assert body["messages"][1]["role"] == "user"


async def test_invalid_schema_gets_one_repair_attempt(settings: Settings) -> None:
    client, queue = make_client(
        settings,
        [
            completion({"questions": []}),
            completion(
                {
                    "questions": [
                        {"missing_piece": "Example", "question": "What happened?"},
                        {"missing_piece": "Lesson", "question": "What changed?"},
                    ]
                }
            ),
        ],
    )
    try:
        result = await client.generate(
            system_instruction="Generate questions.",
            user_prompt="Use this context.",
            schema=GeneratedSectionQuestions,
        )
    finally:
        await client.close()

    assert len(result.content.questions) == 2
    assert result.input_token_count == 24
    assert result.output_token_count == 16
    repair_body = json.loads(queue.requests[1].content)
    assert [message["role"] for message in repair_body["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "Validation errors" in repair_body["messages"][-1]["content"]


@pytest.mark.parametrize(
    "invalid_content",
    ["not-json", json.dumps({"questions": []})],
)
async def test_second_invalid_response_fails(settings: Settings, invalid_content: str) -> None:
    client, queue = make_client(
        settings, [completion(invalid_content), completion(invalid_content)]
    )
    try:
        with pytest.raises(BriefProviderResponseError):
            await client.generate(
                system_instruction="Generate questions.",
                user_prompt="Context.",
                schema=GeneratedSectionQuestions,
            )
    finally:
        await client.close()
    assert len(queue.requests) == 2


async def test_transient_status_retries_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.services.openrouter_ai.asyncio.sleep", no_sleep)
    client, queue = make_client(
        settings,
        [
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            completion(brief_payload()),
        ],
    )
    try:
        result = await client.generate(
            system_instruction="Generate a brief.",
            user_prompt="Context.",
            schema=GeneratedBrief,
        )
    finally:
        await client.close()
    assert result.content.summary == "A focused summary."
    assert len(queue.requests) == 2


@pytest.mark.parametrize(
    ("response_factory", "expected_error"),
    [
        (lambda: httpx.Response(401, text="invalid key"), BriefProviderUnavailableError),
        (
            lambda: httpx.Response(403, text="blocked by moderation policy"),
            BriefProviderBlockedError,
        ),
        (
            lambda: completion({"summary": "ignored"}, model=123),
            BriefProviderResponseError,
        ),
    ],
)
async def test_openrouter_errors_are_mapped(
    settings: Settings,
    response_factory: Callable[[], httpx.Response],
    expected_error: type[Exception],
) -> None:
    responses = [response_factory()]
    if expected_error is BriefProviderResponseError:
        responses.append(response_factory())
    client, _queue = make_client(settings, responses)
    try:
        with pytest.raises(expected_error):
            await client.generate(
                system_instruction="Generate.",
                user_prompt="Context.",
                schema=GeneratedBrief,
            )
    finally:
        await client.close()


async def test_timeout_is_mapped(settings: Settings) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    client, _queue = make_client(settings, [httpx.ReadTimeout("timed out", request=request)])
    try:
        with pytest.raises(BriefProviderTimeoutError):
            await client.generate(
                system_instruction="Generate.",
                user_prompt="Context.",
                schema=GeneratedBrief,
            )
    finally:
        await client.close()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_malformed_success_fails_after_repair(
    settings: Settings, body: dict[str, object]
) -> None:
    response = httpx.Response(200, json=body)
    client, queue = make_client(settings, [response, httpx.Response(200, json=body)])
    try:
        with pytest.raises(BriefProviderResponseError):
            await client.generate(
                system_instruction="Generate.",
                user_prompt="Context.",
                schema=GeneratedBrief,
            )
    finally:
        await client.close()
    assert len(queue.requests) == 2


@pytest.mark.parametrize(
    "choice",
    [
        {"finish_reason": "content_filter", "message": {"content": "{}"}},
        {"finish_reason": "stop", "message": {"content": "{}", "refusal": "No"}},
    ],
)
async def test_explicit_refusal_is_blocked(settings: Settings, choice: dict[str, object]) -> None:
    client, _queue = make_client(settings, [httpx.Response(200, json={"choices": [choice]})])
    try:
        with pytest.raises(BriefProviderBlockedError):
            await client.generate(
                system_instruction="Generate.",
                user_prompt="Context.",
                schema=GeneratedBrief,
            )
    finally:
        await client.close()


async def test_malformed_usage_and_model_metadata_are_ignored(settings: Settings) -> None:
    response = completion(brief_payload(), model=123)
    body = response.json()
    body["usage"] = {"prompt_tokens": "many", "completion_tokens": True}
    client, _queue = make_client(settings, [httpx.Response(200, json=body)])
    try:
        result = await client.generate(
            system_instruction="Generate.",
            user_prompt="Context.",
            schema=GeneratedBrief,
        )
    finally:
        await client.close()

    assert result.model_id == "deepseek/deepseek-v4-pro-0813"
    assert result.input_token_count is None
    assert result.output_token_count is None
