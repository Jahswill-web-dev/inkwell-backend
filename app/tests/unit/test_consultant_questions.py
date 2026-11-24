from uuid import uuid4

import pytest

from app.core.exceptions import AppError
from app.db.models.consultant import ConsultantInterview
from app.db.models.voice_session import VoiceSession
from app.schemas.consultant import VoiceQuestionDelivery, VoiceQuestionInput
from app.services.consultant_questions import (
    acknowledge_question,
    clear_pending_question,
    question_state,
    register_question,
)


def records() -> tuple[ConsultantInterview, VoiceSession]:
    interview = ConsultantInterview(id=uuid4(), state="active", voice_state={"other_state": True})
    voice = VoiceSession(
        id=uuid4(),
        consultant_interview_id=interview.id,
        status="connected",
        provider_metadata={},
    )
    return interview, voice


def selected(text: str = "What happened at the handoff?") -> VoiceQuestionInput:
    return VoiceQuestionInput(
        question=text,
        spoken_text=f"Thanks for explaining. {text}",
        kind="follow_up",
    )


def test_question_remains_visible_until_next_confirmed_delivery() -> None:
    interview, voice = records()
    question = register_question(interview, voice, "first", selected())
    assert question_state(interview).current_question is None
    assert register_question(interview, voice, "first", selected()).id == question.id
    assert interview.voice_state["question_sequence"] == 1
    delivery = VoiceQuestionDelivery(status="delivered", transcript_item_ids=["spoken-1"])
    turns = [
        {
            "item_id": "spoken-1",
            "speaker": "interviewer",
            "voice_session_id": str(voice.id),
            "text": "Thanks for explaining. What happened at the handoff?",
        }
    ]
    state = acknowledge_question(interview, voice, question.id, delivery, turns)
    assert state.current_question is not None
    assert state.current_question.text == question.text
    assert state.current_question.delivered_at is not None
    assert state.pending_question is None
    assert acknowledge_question(interview, voice, question.id, delivery, turns) == state

    next_question = register_question(interview, voice, "second", selected("What did you change?"))
    assert question_state(interview).current_question == state.current_question
    acknowledge_question(
        interview,
        voice,
        next_question.id,
        VoiceQuestionDelivery(status="interrupted"),
        [],
    )
    assert question_state(interview).current_question == state.current_question
    assert question_state(interview).pending_question is None
    clear_pending_question(interview)
    assert question_state(interview).current_question == state.current_question
    assert interview.voice_state["other_state"] is True


@pytest.mark.parametrize(
    "turn",
    [
        None,
        {"speaker": "participant", "text": "What happened at the handoff?"},
        {"speaker": "interviewer", "text": "Thanks, that is helpful."},
        {"speaker": "interviewer", "text": "What happened at the"},
        {"speaker": "interviewer", "text": "What did not happen at the handoff?"},
        {
            "speaker": "interviewer",
            "text": "What happened at the handoff?",
            "voice_session_id": str(uuid4()),
        },
    ],
)
def test_delivery_requires_matching_saved_interviewer_words(turn: dict[str, str] | None) -> None:
    interview, voice = records()
    question = register_question(interview, voice, "q", selected())
    turns = [{"item_id": "spoken", "voice_session_id": str(voice.id), **turn}] if turn else []
    with pytest.raises(AppError, match="matching interviewer transcript"):
        acknowledge_question(
            interview,
            voice,
            question.id,
            VoiceQuestionDelivery(status="delivered", transcript_item_ids=["spoken"]),
            turns,
        )
    assert question_state(interview).current_question is None
    assert question_state(interview).pending_question is not None


def test_matching_ignores_transcription_punctuation_but_not_word_changes() -> None:
    interview, voice = records()
    question = register_question(interview, voice, "q", selected())
    state = acknowledge_question(
        interview,
        voice,
        question.id,
        VoiceQuestionDelivery(status="delivered", transcript_item_ids=["spoken"]),
        [
            {
                "item_id": "spoken",
                "speaker": "interviewer",
                "voice_session_id": str(voice.id),
                "text": "Thanks. WHAT happened\n at the handoff",
            }
        ],
    )
    assert state.current_question is not None


def test_late_delivery_cannot_replace_a_newer_question() -> None:
    interview, voice = records()
    older = register_question(interview, voice, "older", selected())
    newer = register_question(interview, voice, "newer", selected("What did you change?"))
    with pytest.raises(AppError, match="no longer awaiting delivery"):
        acknowledge_question(
            interview,
            voice,
            older.id,
            VoiceQuestionDelivery(status="interrupted"),
            [],
        )
    assert question_state(interview).pending_question == newer
    assert register_question(interview, voice, "older", selected()).status == "superseded"
    assert question_state(interview).pending_question == newer


@pytest.mark.parametrize(
    "state,status", [("paused", "connected"), ("completed", "connected"), ("active", "paused")]
)
def test_old_or_stopped_sessions_cannot_register_questions(state: str, status: str) -> None:
    interview, voice = records()
    interview.state, voice.status = state, status
    with pytest.raises(AppError, match="no longer active"):
        register_question(interview, voice, "q", selected())


def test_reconnection_keeps_current_and_invalidates_old_pending() -> None:
    interview, voice = records()
    old = register_question(interview, voice, "old", selected())
    clear_pending_question(interview)
    voice.status = "paused"
    replacement = VoiceSession(
        id=uuid4(),
        consultant_interview_id=interview.id,
        status="connected",
        provider_metadata={},
    )
    new = register_question(interview, replacement, "new", selected())
    assert new.sequence == old.sequence + 1
    with pytest.raises(AppError):
        acknowledge_question(
            interview,
            voice,
            old.id,
            VoiceQuestionDelivery(status="interrupted"),
            [],
        )
    assert question_state(interview).pending_question == new


def test_structured_question_must_be_part_of_the_approved_spoken_response() -> None:
    with pytest.raises(ValueError, match="exact question"):
        VoiceQuestionInput(question="What happened?", spoken_text="What changed?", kind="question")
    with pytest.raises(ValueError):
        VoiceQuestionInput(question=" ", spoken_text=" ", kind="question")
    with pytest.raises(ValueError):
        VoiceQuestionDelivery(status="delivered")


def test_reused_tool_id_cannot_change_the_registered_question() -> None:
    interview, voice = records()
    first = register_question(interview, voice, "q", selected())
    with pytest.raises(AppError, match="already registered another question"):
        register_question(interview, voice, "q", selected("What changed?"))
    assert question_state(interview).pending_question == first


def test_delivery_can_match_a_question_split_across_saved_fragments() -> None:
    interview, voice = records()
    question = register_question(interview, voice, "q", selected())
    turns = [
        {
            "item_id": "part-1",
            "speaker": "interviewer",
            "voice_session_id": str(voice.id),
            "text": "Thanks. What happened",
        },
        {
            "item_id": "part-2",
            "speaker": "interviewer",
            "voice_session_id": str(voice.id),
            "text": "at the handoff?",
        },
    ]
    delivery = VoiceQuestionDelivery(status="delivered", transcript_item_ids=["part-1", "part-2"])
    state = acknowledge_question(interview, voice, question.id, delivery, turns)
    assert state.current_question is not None
    assert state.current_question.text == "What happened at the handoff?"
    repeated = register_question(interview, voice, "repeat", selected())
    # Re-asking requires new delivered audio, not replaying a previous saved turn.
    with pytest.raises(AppError, match="matching interviewer transcript"):
        acknowledge_question(interview, voice, repeated.id, delivery, turns)


@pytest.mark.parametrize(
    "ids,speakers",
    [
        (["part-2", "part-1"], ["interviewer", "interviewer"]),
        (["part-1", "part-2"], ["interviewer", "participant"]),
        (["part-1", "missing"], ["interviewer", "interviewer"]),
    ],
)
def test_fragment_matching_rejects_wrong_order_missing_or_participant_turns(
    ids: list[str],
    speakers: list[str],
) -> None:
    interview, voice = records()
    question = register_question(interview, voice, "q", selected())
    turns = [
        {
            "item_id": "part-1",
            "speaker": speakers[0],
            "voice_session_id": str(voice.id),
            "text": "What happened",
        },
        {
            "item_id": "part-2",
            "speaker": speakers[1],
            "voice_session_id": str(voice.id),
            "text": "at the handoff?",
        },
    ]
    with pytest.raises(AppError, match="matching interviewer transcript"):
        acknowledge_question(
            interview,
            voice,
            question.id,
            VoiceQuestionDelivery(status="delivered", transcript_item_ids=ids),
            turns,
        )
