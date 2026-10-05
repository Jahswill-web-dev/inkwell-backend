from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.exceptions import AppError
from app.schemas.interview_transcript import TranscriptTurn
from app.schemas.realtime import VoiceSessionCreate
from app.services.voice_interview import (
    ElevenLabsVoiceProvider,
    OpenAILiveVoiceProvider,
    _elevenlabs_dynamic_variables,
    create_voice_provider,
)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@localhost/database",
        "jwt_secret_key": "a-secure-test-secret-with-32-characters",
        "openai_api_key": "test-openai-key",
        "elevenlabs_api_key": "test-eleven-key",
        "elevenlabs_agent_id": "agent_test",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_factory_uses_only_the_server_selected_provider() -> None:
    session = MagicMock()

    assert isinstance(
        create_voice_provider(session, settings(voice_interview_provider="openai_live")),
        OpenAILiveVoiceProvider,
    )
    assert isinstance(
        create_voice_provider(session, settings(voice_interview_provider="elevenlabs")),
        ElevenLabsVoiceProvider,
    )


async def test_elevenlabs_rejects_client_sdp_without_calling_provider() -> None:
    provider = ElevenLabsVoiceProvider(
        MagicMock(), settings(voice_interview_provider="elevenlabs")
    )

    with pytest.raises(AppError) as error:
        await provider.create_session(
            payload=VoiceSessionCreate(sdp="client-offer"),
            interview=MagicMock(),
            previous_turns=[],
        )

    assert error.value.code == "voice_sdp_not_supported"


async def test_elevenlabs_accepts_token_response_without_conversation_id() -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.add.side_effect = lambda record: setattr(
        record, "id", UUID("22222222-2222-4222-8222-222222222222")
    )
    response = MagicMock()
    response.json.return_value = {"token": "temporary-token"}
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    interview = SimpleNamespace(
        invitation=SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            participant_name="Taylor",
            client_name="Example Co",
            writer_name="Morgan",
            article_title="A useful article",
        ),
        session=SimpleNamespace(questions=[]),
    )
    provider = ElevenLabsVoiceProvider(
        session, settings(voice_interview_provider="elevenlabs")
    )

    with patch("app.services.voice_interview.httpx.AsyncClient", return_value=context):
        result = await provider.create_session(
            payload=VoiceSessionCreate(), interview=interview, previous_turns=[]
        )

    assert result.conversation_token == "temporary-token"
    record = session.add.call_args.args[0]
    assert record.external_session_id is None


def test_elevenlabs_dynamic_variables_include_resume_workflow() -> None:
    interview = SimpleNamespace(
        invitation=SimpleNamespace(
            participant_name="Taylor",
            client_name="Example Co",
            writer_name="Morgan",
            article_title="A useful article",
        ),
        session=SimpleNamespace(
            questions=[SimpleNamespace(text="What changed?")],
        ),
    )

    variables = _elevenlabs_dynamic_variables(
        interview,
        [TranscriptTurn(item_id="turn-1", speaker="participant", text="A prior answer")],
    )

    assert variables["participant_name"] == "Taylor"
    assert variables["is_resumed"] is True
    assert "What changed?" in str(variables["interview_instructions"])
    assert "A prior answer" in str(variables["interview_instructions"])


async def test_openai_provider_requires_sdp() -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    with patch("app.services.voice_interview.OpenAIRealtimeService"):
        provider = OpenAILiveVoiceProvider(session, settings())

    with pytest.raises(AppError) as error:
        await provider.create_session(
            payload=VoiceSessionCreate(),
            interview=MagicMock(),
            previous_turns=[],
        )

    assert error.value.code == "voice_sdp_required"
