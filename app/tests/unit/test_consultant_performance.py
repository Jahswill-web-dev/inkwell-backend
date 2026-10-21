from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.consultant_performance import percentile, summarize
from app.services.consultant_writer import provider_request_id


def test_event_totals_missing_tokens_and_multiple_models() -> None:
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    job = SimpleNamespace(
        id=uuid4(),
        article_id=uuid4(),
        created_at=started,
        snapshot={"request_kind": "ask_ai"},
        state="succeeded",
        attempts=2,
        model_id="fallback-model",
        usage={"total_wall_seconds": 99},
    )

    def event(
        kind: str,
        attempt: int,
        offset: int,
        duration: int,
        *,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        outcome: str = "completed",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            kind=kind,
            operation="rate_limit" if kind == "wait" else None,
            attempt=attempt,
            started_at=started + timedelta(seconds=offset),
            ended_at=started + timedelta(seconds=offset + duration),
            duration_ms=duration * 1000,
            model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            outcome=outcome,
        )

    events = [
        event("queue", 0, 0, 5),
        event("attempt", 1, 5, 15, outcome="failed"),
        event("queue", 1, 20, 10),
        event("attempt", 2, 30, 20),
        event("call", 1, 6, 4, model="writer-model", outcome="rate_limited"),
        event("wait", 1, 10, 7, model="writer-model"),
        event(
            "call",
            2,
            31,
            8,
            model="checker-model",
            input_tokens=30,
            output_tokens=None,
            outcome="succeeded",
        ),
    ]
    result = summarize(job, "Article", events)
    assert result["queue_seconds"] == 15
    assert result["active_seconds"] == 35
    assert result["rate_limit_seconds"] == 7
    assert result["total_seconds"] == 50
    assert result["queue_seconds"] + result["active_seconds"] == result["total_seconds"]
    assert result["models"] == ["checker-model", "writer-model"]
    assert result["total_tokens"] == 30
    assert result["detailed_timing_available"]


def test_historical_job_does_not_invent_details() -> None:
    job = SimpleNamespace(
        id=uuid4(),
        article_id=uuid4(),
        created_at=datetime.now(UTC),
        snapshot={},
        state="succeeded",
        attempts=1,
        model_id="old-model",
        usage={"total_wall_seconds": 82, "input_tokens": 12},
    )
    result = summarize(job, "Older article", [])
    assert result["detailed_timing_available"] is False
    assert result["total_seconds"] == 82
    assert result["queue_seconds"] is None
    assert result["total_tokens"] == 12
    assert percentile([10, 20, 30], 0.95) == 29


def test_provider_request_id_is_taken_without_error_body() -> None:
    error = RuntimeError("private provider body")
    error.response = SimpleNamespace(headers={"x-request-id": "req-123"})
    assert provider_request_id(error) == "req-123"
