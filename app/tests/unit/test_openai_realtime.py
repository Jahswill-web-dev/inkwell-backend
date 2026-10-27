from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.schemas.interview_invitation import (
    GuestInterviewResponse,
    GuestQuestion,
    GuestSessionData,
    InterviewInvitationResponse,
)
from app.schemas.interview_transcript import TranscriptTurn
from app.services.interview_context import ConsultantVoiceContext
from app.services.openai_realtime import (
    END_INTERVIEW_TOOL,
    OpenAIRealtimeService,
    _end_interview_tool_output,
    _run_live_sideband_controller,
    build_interview_workflow_instructions,
    build_live_voice_instructions,
)


def test_workflow_instructions_use_invitation_and_question_plan() -> None:
    now = datetime.now(UTC)
    interview = GuestInterviewResponse(
        invitation=InterviewInvitationResponse(
            id=uuid4(),
            article_id=uuid4(),
            article_title="How teams use customer research",
            client_name="Northstar Labs",
            writer_name="Morgan Lee",
            participant_name="Avery Chen",
            participant_email="avery@example.com",
            token="x" * 48,
            status="active",
            expires_at=None,
            progress_state="opened",
            questions_answered=0,
            estimated_questions=2,
            opened_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        ),
        session=GuestSessionData(
            token="x" * 48,
            questions=[
                GuestQuestion(id="q1", text="What problem were you trying to solve?"),
                GuestQuestion(id="q2", text="What changed after you solved it?"),
            ],
        ),
    )

    instructions = build_interview_workflow_instructions(interview)

    assert "Participant: Avery Chen" in instructions
    assert "Client: Northstar Labs" in instructions
    assert "Article title: How teams use customer research" in instructions
    assert "1. What problem were you trying to solve?" in instructions
    assert "2. What changed after you solved it?" in instructions
    assert "Ask one question at a time" in instructions
    assert "Open with a brief orientation before any interview question" in instructions
    assert "help Morgan Lee shape the article “How teams use customer research”" in instructions
    assert "ask whether the participant is ready to begin" in instructions
    assert "Do not ask the first\n  planned question until they clearly confirm" in instructions
    assert "do\n  not treat silence as consent to begin" in instructions
    assert "end_interview with\n  reason participant_finished" in instructions
    assert "end_interview with reason questions_complete" in instructions
    assert "Is there anything else you'd like to add to the article?" in instructions
    assert "Do not end the interview\n  before they answer this final question" in instructions
    assert "next natural pause" in instructions
    assert "cost schedule" in instructions
    assert "does not count\n  as the optional follow-up" in instructions


def test_end_interview_tool_accepts_only_completion_reasons() -> None:
    assert END_INTERVIEW_TOOL["name"] == "end_interview"
    assert END_INTERVIEW_TOOL["parameters"]["properties"]["reason"]["enum"] == [
        "participant_finished",
        "questions_complete",
    ]


def test_workflow_instructions_resume_from_persisted_transcript() -> None:
    now = datetime.now(UTC)
    interview = GuestInterviewResponse(
        invitation=InterviewInvitationResponse(
            id=uuid4(), article_id=uuid4(), article_title="Research", client_name="Northstar",
            writer_name="Morgan", participant_name="Avery", participant_email="avery@example.com",
            token="x" * 48, status="active", expires_at=None, progress_state="opened",
            questions_answered=0, estimated_questions=1, opened_at=now, completed_at=None,
            created_at=now, updated_at=now,
        ),
        session=GuestSessionData(
            token="x" * 48,
            questions=[GuestQuestion(id="q1", text="What changed?")],
        ),
    )

    instructions = build_interview_workflow_instructions(
        interview,
        [
            TranscriptTurn(item_id="assistant-1", speaker="interviewer", text="What changed?"),
            TranscriptTurn(
                item_id="participant-1",
                speaker="participant",
                text="We reduced research time.",
            ),
        ],
    )

    assert "This is a replacement connection after an interruption" in instructions
    assert "Interviewer: What changed?" in instructions
    assert "Participant: We reduced research time." in instructions
    assert "Do not repeat your opening\norientation" in instructions


def test_live_voice_instructions_are_concise_and_delegate_workflow() -> None:
    now = datetime.now(UTC)
    interview = GuestInterviewResponse(
        invitation=InterviewInvitationResponse(
            id=uuid4(), article_id=uuid4(), article_title="Research", client_name="Northstar",
            writer_name="Morgan", participant_name="Avery", participant_email="avery@example.com",
            token="x" * 48, status="active", expires_at=None, progress_state="opened",
            questions_answered=0, estimated_questions=1, opened_at=now, completed_at=None,
            created_at=now, updated_at=now,
        ),
        session=GuestSessionData(token="x" * 48, questions=[]),
    )

    instructions = build_live_voice_instructions(interview)

    assert "Delegate question, follow-up, and completion workflow decisions" in instructions
    assert "next natural pause" in instructions
    assert "<interview_plan>" not in instructions
    assert "1. " not in instructions


def test_consultant_voice_uses_separate_delivery_and_adaptive_workflow_prompts() -> None:
    interview = ConsultantVoiceContext(
        "Jo",
        "Practical AI for engineers",
        "How engineers can use AI without replacing judgement",
        ["Working engineers"],
        [
            {"id": "0", "text": "The article's central argument"},
            # A legacy interview may still contain question-shaped records.
            {"id": "legacy", "text": "Can you walk me through a specific example?"},
        ],
    )

    delivery = build_live_voice_instructions(interview)
    workflow = build_interview_workflow_instructions(interview)
    compact_delivery = " ".join(delivery.split())
    compact_workflow = " ".join(workflow.split())

    assert "Speak only the application-approved commentary" in compact_delivery
    assert "must not replace its question" in compact_delivery
    assert "Do not call tools" in compact_delivery
    assert "Follow these phases in order" not in delivery
    assert "greet the consultant by name" in compact_workflow
    assert "mention the proposed article title" in compact_workflow
    assert "summarise what its description says the article will explore" in compact_workflow
    assert "name the intended audience when one is supplied" in compact_workflow
    assert "Then ask one easy, substantive question" in compact_workflow
    assert "Do not add a separate readiness check" in compact_workflow
    assert "Do not open by asking for the article's main argument" in compact_workflow
    assert "Do not use the same stock opening question for every article" in compact_workflow
    assert "Ask for or confirm the central argument only after" in compact_workflow
    assert "planning context only" in compact_workflow
    assert "not evidence that the consultant personally made any claim" in compact_workflow
    assert "flexible objectives, never as a script" in compact_workflow
    assert "From the first answer, follow the most useful thread" in compact_workflow
    assert "Skip objectives already answered and drop irrelevant ones" in compact_workflow
    assert "at most two optional follow-ups" in compact_workflow
    assert "call end_interview with reason participant_finished" in compact_workflow
    assert "call end_interview with reason questions_complete" in compact_workflow
    assert "Is there anything else you'd like to add to the article?" in compact_workflow
    assert "Do not end before the final answer" in compact_workflow
    assert "Do not require a fixed recap or extra confirmation" in compact_workflow
    assert '"article_title": "Practical AI for engineers"' in workflow
    assert (
        '"article_description": "How engineers can use AI without replacing judgement"'
        in workflow
    )
    assert '"intended_audience": ["Working engineers"]' in workflow
    assert '"coverage_objectives"' in workflow


def test_consultant_voice_resume_continues_without_restarting() -> None:
    interview = ConsultantVoiceContext(
        "Jo", "Handoffs", "Why clear ownership matters", ["Founders"], []
    )
    turns = [
        TranscriptTurn(item_id="a1", speaker="interviewer", text="Are you ready?"),
        TranscriptTurn(item_id="u1", speaker="participant", text="Yes, I am ready."),
        TranscriptTurn(
            item_id="a2",
            speaker="interviewer",
            text="It sounds like this is about clear ownership. Is that accurate?",
        ),
        TranscriptTurn(
            item_id="u2",
            speaker="participant",
            text="Yes, specifically ownership during agency handoffs.",
        ),
    ]

    workflow = build_interview_workflow_instructions(interview, turns)

    assert "For a resumed interview, briefly acknowledge the interruption" in workflow
    assert (
        "Do not repeat the article introduction, opening question, or answered questions"
        in workflow
    )
    assert "Participant: Yes, specifically ownership during agency handoffs." in workflow


def test_completion_tool_output_rejects_invalid_arguments() -> None:
    assert _end_interview_tool_output('{"reason":"questions_complete"}') == {
        "accepted": True,
        "reason": "questions_complete",
    }
    assert _end_interview_tool_output('{"reason":"participant_finished"}') == {
        "accepted": True,
        "reason": "participant_finished",
    }
    assert _end_interview_tool_output('{"reason":"anything_else"}') == {
        "accepted": False,
        "error": "invalid_completion_reason",
    }


class _FakeSidebandConnection:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        call = {
            "type": "function_call",
            "call_id": "call-1",
            "name": "end_interview",
            "arguments": '{"reason":"questions_complete"}',
        }
        self.events = [
            SimpleNamespace(
                type="response.event",
                event={"type": "response.output_item.done", "item": call},
            ),
            SimpleNamespace(
                type="response.event",
                event={"type": "response.output_item.done", "item": call},
            ),
            SimpleNamespace(type="session.closed"),
        ]

    async def __aenter__(self) -> "_FakeSidebandConnection":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def __aiter__(self) -> "_FakeSidebandConnection":
        self._index = 0
        return self

    async def __anext__(self) -> object:
        if self._index >= len(self.events):
            raise StopAsyncIteration
        event = self.events[self._index]
        self._index += 1
        return event

    async def send(self, event: dict[str, object]) -> None:
        self.sent.append(event)


async def test_sideband_executes_each_completion_call_once() -> None:
    connection = _FakeSidebandConnection()
    client = SimpleNamespace(
        live=SimpleNamespace(
            sideband=SimpleNamespace(connect=lambda **_: connection),
        )
    )

    await _run_live_sideband_controller(client, "live-test")

    assert len(connection.sent) == 2
    assert connection.sent[0]["type"] == "response.item.create"
    assert connection.sent[1] == {"type": "response.create"}


async def test_live_delegation_uses_sol_with_medium_reasoning() -> None:
    create = AsyncMock(
        return_value=SimpleNamespace(
            session=SimpleNamespace(id="live-session"),
            transport=SimpleNamespace(sdp="answer-sdp"),
        )
    )
    service = object.__new__(OpenAIRealtimeService)
    service._client = SimpleNamespace(live=SimpleNamespace(create=create))
    service._model = "gpt-live-1"
    service._workflow_model = "gpt-5.6-sol"
    service._reasoning_effort = "medium"
    interview = ConsultantVoiceContext("Jo", "Handoffs", "Broken handoffs", [], [])

    with patch("app.services.openai_realtime.start_live_sideband_controller"):
        result = await service.create_call(sdp="offer-sdp", interview=interview)

    assert result.external_session_id == "live-session"
    session = create.await_args.kwargs["session"]
    assert session["model"] == "gpt-live-1"
    assert session["delegation"]["responses"]["model"] == "gpt-5.6-sol"
    assert session["delegation"]["responses"]["reasoning"] == {"effort": "medium"}
    assert session["delegation"]["responses"]["tools"] == [END_INTERVIEW_TOOL]
    assert session["delegation"]["responses"]["tool_choice"] == "auto"
