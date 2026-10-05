from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.session import get_db_session
from app.services.interview_transcript_service import InterviewTranscriptService

router = APIRouter(tags=["voice webhooks"])


@router.post("/webhooks/elevenlabs")
async def receive_elevenlabs_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    body = await request.body()
    _verify_signature(
        body,
        request.headers.get("elevenlabs-signature"),
        settings,
    )
    try:
        event = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AppError(
            status_code=400,
            code="invalid_webhook_payload",
            message="The webhook payload was not valid JSON",
        ) from exc
    if not isinstance(event, dict) or event.get("type") != "post_call_transcription":
        return {"received": True}
    data = event.get("data")
    if not isinstance(data, dict):
        raise AppError(
            status_code=422,
            code="invalid_webhook_payload",
            message="The webhook payload was invalid",
        )
    conversation_id = data.get("conversation_id")
    transcript = data.get("transcript")
    if not isinstance(conversation_id, str) or not isinstance(transcript, list):
        raise AppError(
            status_code=422,
            code="invalid_webhook_payload",
            message="The webhook payload was invalid",
        )

    service = InterviewTranscriptService(
        session,
        generator=getattr(request.app.state, "interview_insights_generator", None),
    )
    _, changed, invitation_token = await service.reconcile_elevenlabs_transcript(
        external_session_id=conversation_id,
        provider_turns=[item for item in transcript if isinstance(item, dict)],
        provider_metadata=_provider_metadata(data),
    )
    if changed and invitation_token:
        await service.finalize(token=invitation_token)
    return {"received": True}


def _verify_signature(body: bytes, signature: str | None, settings: Settings) -> None:
    if settings.elevenlabs_webhook_secret is None:
        raise AppError(
            status_code=503,
            code="webhook_not_configured",
            message="The ElevenLabs webhook is not configured",
        )
    timestamp: str | None = None
    supplied: str | None = None
    for part in (signature or "").split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        if key == "t":
            timestamp = value
        elif key == "v0":
            supplied = value
    try:
        timestamp_value = int(timestamp or "")
    except ValueError as exc:
        raise AppError(
            status_code=401,
            code="invalid_webhook_signature",
            message="The webhook signature was invalid",
        ) from exc
    if abs(int(time.time()) - timestamp_value) > 300 or supplied is None:
        raise AppError(
            status_code=401,
            code="invalid_webhook_signature",
            message="The webhook signature was invalid",
        )
    assert timestamp is not None
    signed_payload = timestamp.encode() + b"." + body
    expected = hmac.new(
        settings.elevenlabs_webhook_secret.get_secret_value().encode(),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise AppError(
            status_code=401,
            code="invalid_webhook_signature",
            message="The webhook signature was invalid",
        )


def _provider_metadata(data: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = data.get("metadata")
    raw_analysis = data.get("analysis")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    return {
        "call_duration_secs": metadata.get("call_duration_secs"),
        "cost": metadata.get("cost"),
        "termination_reason": metadata.get("termination_reason"),
        "transcript_summary": analysis.get("transcript_summary"),
        "call_successful": analysis.get("call_successful"),
    }
