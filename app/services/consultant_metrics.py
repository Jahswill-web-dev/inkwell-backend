"""Durable, content-free timing events for consultant generation jobs."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.consultant import ConsultantGeneration, ConsultantGenerationEvent


def utcnow() -> datetime:
    return datetime.now(UTC)


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def close_event(
    event: ConsultantGenerationEvent,
    *,
    outcome: str,
    end: datetime | None = None,
    duration_ms: int | None = None,
) -> None:
    finished = end or utcnow()
    event.ended_at = finished
    event.duration_ms = (
        duration_ms
        if duration_ms is not None
        else max(0, round((finished - event.started_at).total_seconds() * 1000))
    )
    event.outcome = outcome


async def open_event(
    db: AsyncSession,
    job_id: UUID,
    attempt: int,
    kind: str,
    operation: str | None = None,
    model_id: str | None = None,
    started_at: datetime | None = None,
) -> ConsultantGenerationEvent:
    event = ConsultantGenerationEvent(
        generation_id=job_id,
        attempt=attempt,
        kind=kind,
        operation=operation,
        model_id=model_id,
        started_at=started_at or utcnow(),
        outcome="running",
    )
    db.add(event)
    await db.flush()
    return event


async def close_open_events(
    db: AsyncSession,
    job_id: UUID,
    attempt: int,
    outcome: str,
    end: datetime | None = None,
    duration_by_kind: dict[str, int] | None = None,
) -> None:
    events = (
        await db.scalars(
            select(ConsultantGenerationEvent)
            .where(
                ConsultantGenerationEvent.generation_id == job_id,
                ConsultantGenerationEvent.attempt == attempt,
                ConsultantGenerationEvent.ended_at.is_(None),
            )
            .with_for_update()
        )
    ).all()
    for event in events:
        close_event(
            event,
            outcome=outcome,
            end=max(end, event.started_at) if end else None,
            duration_ms=(duration_by_kind or {}).get(event.kind),
        )


class MetricsRecorder:
    """Writes before and after provider work; a failed write aborts the worker attempt."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        job_id: UUID,
        attempt: int,
        lease_token: str,
    ) -> None:
        self.factory = factory
        self.job_id = job_id
        self.attempt = attempt
        self.lease_token = lease_token

    async def _assert_lease(self, db: AsyncSession) -> None:
        job = await db.scalar(
            select(ConsultantGeneration)
            .where(ConsultantGeneration.id == self.job_id)
            .with_for_update()
        )
        if job is None or job.state != "running" or job.lease_token != self.lease_token:
            raise RuntimeError("Generation lease lost while recording performance")

    async def start(
        self, kind: str, operation: str | None = None, model_id: str | None = None
    ) -> tuple[UUID, float]:
        async with self.factory.begin() as db:
            await self._assert_lease(db)
            event = await open_event(db, self.job_id, self.attempt, kind, operation, model_id)
            event_id = event.id
        return event_id, time.perf_counter()

    async def finish(
        self,
        event_id: UUID,
        clock: float,
        outcome: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        duration = elapsed_ms(clock)
        async with self.factory.begin() as db:
            await self._assert_lease(db)
            event = await db.get(ConsultantGenerationEvent, event_id, with_for_update=True)
            if event is None or event.generation_id != self.job_id or event.ended_at is not None:
                raise RuntimeError("Performance event is missing or already closed")
            close_event(event, outcome=outcome, duration_ms=duration)
            event.input_tokens = input_tokens
            event.output_tokens = output_tokens
            event.provider_request_id = provider_request_id[:128] if provider_request_id else None
