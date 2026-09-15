from datetime import UTC, datetime
from uuid import uuid4

from app.schemas.interview_invitation import (
    GuestInterviewResponse,
    GuestQuestion,
    GuestSessionData,
    InterviewInvitationResponse,
)
from app.services.openai_realtime import END_INTERVIEW_TOOL, build_interview_instructions


def test_build_interview_instructions_uses_invitation_and_question_plan() -> None:
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

    instructions = build_interview_instructions(interview)

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
