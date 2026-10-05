from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.interview_invitation import (
    GuestInterviewResponse,
    GuestQuestion,
    GuestSessionData,
    InterviewInvitationResponse,
)
from app.schemas.interview_transcript import TranscriptTurn
from app.services.openai_realtime import (
    END_INTERVIEW_TOOL,
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
    assert "<interview_plan>" not in instructions
    assert "1. " not in instructions


def test_completion_tool_output_rejects_invalid_arguments() -> None:
    assert _end_interview_tool_output('{"reason":"questions_complete"}') == {
        "accepted": True,
        "reason": "questions_complete",
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
