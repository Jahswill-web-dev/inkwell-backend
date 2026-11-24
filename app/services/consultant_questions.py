"""Persist workflow-selected questions separately from spoken/transcribed replies.

Selecting a question does not prove it was played. The client must acknowledge
completed playback and identify a matching, saved interviewer turn before promotion.
All callers lock the interview first, serializing question and voice lifecycle changes.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.consultant import ConsultantInterview
from app.db.models.voice_session import VoiceSession
from app.schemas.consultant import (
    VoiceQuestion,
    VoiceQuestionDelivery,
    VoiceQuestionInput,
    VoiceQuestionState,
)
from app.services.consultant_service import fail


def question_state(interview: ConsultantInterview) -> VoiceQuestionState:
    state = interview.voice_state or {}
    return VoiceQuestionState(
        current_question=state.get("current_question"),
        pending_question=state.get("pending_question"),
    )


def clear_pending_question(interview: ConsultantInterview) -> None:
    # Keep the last delivered question available across pause and reconnection.
    interview.voice_state = {**(interview.voice_state or {}), "pending_question": None}


def _events(voice: VoiceSession) -> dict[str, Any]:
    return dict((voice.provider_metadata or {}).get("question_events", {}))


def register_question(
    interview: ConsultantInterview,
    voice: VoiceSession,
    call_id: str,
    payload: VoiceQuestionInput,
) -> VoiceQuestion:
    if voice.consultant_interview_id != interview.id:
        raise fail("voice_session_not_found", "Voice session not found.", 404)
    if interview.state != "active" or voice.status != "connected":
        raise fail("voice_session_inactive", "This voice session is no longer active.")
    events = _events(voice)
    if call_id in events:
        existing = VoiceQuestion.model_validate(events[call_id])
        if existing.text != payload.question or existing.kind != payload.kind:
            raise fail(
                "question_call_conflict", "This tool call already registered another question."
            )
        return existing
    state = interview.voice_state or {}
    sequence = int(state.get("question_sequence", 0)) + 1
    question = VoiceQuestion(
        id=uuid4(),
        voice_session_id=voice.id,
        sequence=sequence,
        text=payload.question,
        kind=payload.kind,
        status="pending",
        created_at=datetime.now(UTC),
    )
    for key, event in events.items():
        if event["status"] == "pending":
            events[key] = {**event, "status": "superseded"}
    serialized = question.model_dump(mode="json")
    events[call_id] = serialized
    voice.provider_metadata = {**(voice.provider_metadata or {}), "question_events": events}
    interview.voice_state = {
        **state,
        "question_sequence": sequence,
        "pending_question": serialized,
    }
    return question


async def register_session_question(
    factory: async_sessionmaker[AsyncSession],
    interview_id: UUID,
    voice_session_id: UUID,
    call_id: str,
    payload: VoiceQuestionInput,
) -> VoiceQuestion:
    """Called by the private sideband, with a fresh transaction per tool call."""
    async with factory.begin() as db:
        interview = await db.scalar(
            select(ConsultantInterview)
            .where(ConsultantInterview.id == interview_id)
            .with_for_update()
        )
        voice = await db.scalar(
            select(VoiceSession).where(
                VoiceSession.id == voice_session_id,
                VoiceSession.consultant_interview_id == interview_id,
            )
        )
        if interview is None or voice is None:
            raise fail("voice_session_not_found", "Voice session not found.", 404)
        return register_question(interview, voice, call_id, payload)


def _words(text: str) -> str:
    # Compare literal spoken words, tolerating ASR casing, whitespace and punctuation.
    # This verifies a selected question; it never extracts or invents one.
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def acknowledge_question(
    interview: ConsultantInterview,
    voice: VoiceSession,
    question_id: UUID,
    payload: VoiceQuestionDelivery,
    turns: list[dict[str, Any]],
) -> VoiceQuestionState:
    events = _events(voice)
    entry = next(
        ((key, value) for key, value in events.items() if value["id"] == str(question_id)), None
    )
    if entry is None:
        raise fail("question_not_found", "Question not found in this voice session.", 404)
    key, value = entry
    question = VoiceQuestion.model_validate(value)
    if question.status == payload.status:
        if question.transcript_item_ids != payload.transcript_item_ids:
            raise fail("question_delivery_conflict", "This question was already acknowledged.")
        return question_state(interview)
    state = question_state(interview)
    if (
        interview.state != "active"
        or voice.status != "connected"
        or question.status != "pending"
        or state.pending_question is None
        or state.pending_question.id != question.id
        or state.pending_question.voice_session_id != voice.id
    ):
        raise fail("question_not_pending", "This question is no longer awaiting delivery.")
    if payload.status == "delivered":
        # Live can split one spoken question into several saved transcript fragments.
        # Accept only a contiguous, ordered run from this session's interviewer.
        ids = payload.transcript_item_ids
        start = next((index for index, turn in enumerate(turns) if turn["item_id"] == ids[0]), -1)
        fragments = turns[start : start + len(ids)] if start >= 0 else []
        question_words = _words(question.text)
        if (
            [turn["item_id"] for turn in fragments] != ids
            or any(turn.get("speaker") != "interviewer" for turn in fragments)
            or any(turn.get("voice_session_id") != str(voice.id) for turn in fragments)
            or not question_words
            or f" {question_words} " not in f" {_words(' '.join(t['text'] for t in fragments))} "
            or any(
                set(ids) & set(event.get("transcript_item_ids", []))
                for event in events.values()
                if event["status"] == "delivered"
            )
        ):
            raise fail(
                "question_transcript_mismatch",
                "Save the matching interviewer transcript before acknowledging delivery.",
                422,
            )
        if state.current_question and state.current_question.sequence >= question.sequence:
            raise fail("question_delivery_stale", "A newer question has already been delivered.")
    question.status = payload.status
    question.transcript_item_ids = payload.transcript_item_ids
    question.delivered_at = datetime.now(UTC) if payload.status == "delivered" else None
    serialized = question.model_dump(mode="json")
    events[key] = serialized
    voice.provider_metadata = {**(voice.provider_metadata or {}), "question_events": events}
    interview.voice_state = {
        **(interview.voice_state or {}),
        "pending_question": None,
        **({"current_question": serialized} if payload.status == "delivered" else {}),
    }
    return question_state(interview)
