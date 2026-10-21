"""Private operational metrics for the configured Inkwell owner."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Float, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.consultant import ConsultantGeneration, ConsultantGenerationEvent
from app.db.session import get_db_session

router = APIRouter(prefix="/consultant/performance", tags=["consultant-performance"])
DB = Annotated[AsyncSession, Depends(get_db_session)]


def require_owner(user: CurrentUser, request: Request) -> None:
    configured = request.app.state.settings.generation_performance_owner_email
    if not configured or user.email.casefold() != configured.strip().casefold():
        raise AppError(status_code=403, code="forbidden", message="Not available for this account")


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * fraction, 3
    )


def summarize(
    job: ConsultantGeneration, title: str, events: list[ConsultantGenerationEvent]
) -> dict[str, Any]:
    attempts = [event for event in events if event.kind == "attempt"]
    calls = [event for event in events if event.kind == "call"]
    queued = [event for event in events if event.kind == "queue"]
    waits = [event for event in events if event.kind == "wait" and event.operation == "rate_limit"]
    ended = [
        event.ended_at
        for event in events
        if event.kind in {"attempt", "outcome"} and event.ended_at
    ]
    terminal = job.state in {"succeeded", "failed", "needs_clarification"}
    finished = max(ended) if ended and terminal else None
    now = datetime.now(UTC)
    lease_until = getattr(job, "lease_until", None)
    live_end = min(now, lease_until) if lease_until and lease_until < now else now

    def milliseconds(event: ConsultantGenerationEvent) -> int:
        if event.duration_ms is not None:
            return event.duration_ms
        return (
            max(0, round((live_end - event.started_at).total_seconds() * 1000))
            if not terminal
            else 0
        )

    detailed = bool(events)
    models = sorted({event.model_id for event in calls if event.model_id})
    if not models and job.model_id:
        models = [job.model_id]
    usage = job.usage or {}
    tokens = (
        sum(
            (event.input_tokens or 0) + (event.output_tokens or 0)
            for event in calls
            if event.outcome == "succeeded"
        )
        if detailed
        else ((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0))
    )
    total = (
        round((finished - job.created_at).total_seconds(), 3)
        if finished
        else round((now - job.created_at).total_seconds(), 3)
        if not terminal and detailed
        else usage.get("total_wall_seconds")
        if not detailed
        else None
    )
    return {
        "id": job.id,
        "article_id": job.article_id,
        "article_title": title,
        "request_type": job.snapshot.get("request_kind", "generation"),
        "status": job.state,
        "queued_at": job.created_at,
        "first_claimed_at": min((event.started_at for event in attempts), default=None),
        "finished_at": finished,
        "attempts": job.attempts,
        "models": models,
        "queue_seconds": round(sum(milliseconds(event) for event in queued) / 1000, 3)
        if detailed
        else usage.get("queue_seconds"),
        "active_seconds": round(sum(milliseconds(event) for event in attempts) / 1000, 3)
        if detailed
        else usage.get("stage_seconds", {}).get("total"),
        "rate_limit_seconds": round(sum(milliseconds(event) for event in waits) / 1000, 3)
        if detailed
        else usage.get("rate_limit_wait_seconds"),
        "total_seconds": total,
        "total_tokens": tokens,
        "detailed_timing_available": detailed,
    }


def serialize_event(event: ConsultantGenerationEvent, lease_expired: bool) -> dict[str, Any]:
    return {
        "id": event.id,
        "attempt": event.attempt,
        "kind": event.kind,
        "operation": event.operation,
        "model_id": event.model_id,
        "started_at": event.started_at,
        "ended_at": event.ended_at,
        "duration_ms": event.duration_ms,
        "outcome": "interrupted" if event.ended_at is None and lease_expired else event.outcome,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "provider_request_id": event.provider_request_id,
    }


@router.get("/access", dependencies=[Depends(require_owner)])
async def access() -> dict[str, bool]:
    return {"allowed": True}


@router.get("/jobs", dependencies=[Depends(require_owner)])
async def list_jobs(
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    from_date: date | None = None,
    to_date: date | None = None,
    request_type: str | None = None,
    outcome: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    query = (
        select(ConsultantGeneration, Article.working_title)
        .join(Article, Article.id == ConsultantGeneration.article_id)
        .where(Article.experience == "consultant")
    )
    if from_date:
        query = query.where(
            ConsultantGeneration.created_at >= datetime.combine(from_date, time.min, UTC)
        )
    if to_date:
        query = query.where(
            ConsultantGeneration.created_at <= datetime.combine(to_date, time.max, UTC)
        )
    if request_type in {"ask_ai", "generation"}:
        request_value = ConsultantGeneration.snapshot["request_kind"].astext
        query = query.where(
            or_(request_value == request_type, request_value.is_(None))
            if request_type == "generation"
            else request_value == request_type
        )
    if outcome in {"queued", "running", "succeeded", "failed", "needs_clarification"}:
        query = query.where(ConsultantGeneration.state == outcome)
    if model:
        query = query.where(
            or_(
                ConsultantGeneration.model_id == model,
                select(ConsultantGenerationEvent.id)
                .where(
                    ConsultantGenerationEvent.generation_id == ConsultantGeneration.id,
                    ConsultantGenerationEvent.model_id == model,
                )
                .exists(),
            )
        )
    filtered = query.with_only_columns(
        ConsultantGeneration.id.label("job_id"),
        ConsultantGeneration.state.label("job_state"),
        ConsultantGeneration.usage["total_wall_seconds"].astext.label("total_seconds"),
    ).subquery()
    total = await db.scalar(select(func.count()).select_from(filtered))
    stats = (
        await db.execute(
            select(
                func.percentile_cont(0.5).within_group(cast(filtered.c.total_seconds, Float)),
                func.percentile_cont(0.95).within_group(cast(filtered.c.total_seconds, Float)),
            ).where(filtered.c.job_state == "succeeded")
        )
    ).one()
    rows = (
        await db.execute(
            query.order_by(ConsultantGeneration.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    jobs = [row[0] for row in rows]
    all_events = (
        (
            await db.scalars(
                select(ConsultantGenerationEvent).where(
                    ConsultantGenerationEvent.generation_id.in_([job.id for job in jobs])
                )
            )
        ).all()
        if jobs
        else []
    )
    by_job: dict[UUID, list[ConsultantGenerationEvent]] = {}
    for event in all_events:
        by_job.setdefault(event.generation_id, []).append(event)
    summaries = [summarize(job, title, by_job.get(job.id, [])) for job, title in rows]
    return {
        "items": summaries,
        "page": page,
        "page_size": page_size,
        "total": total or 0,
        "median_seconds": round(stats[0], 3) if stats[0] is not None else None,
        "p95_seconds": round(stats[1], 3) if stats[1] is not None else None,
    }


@router.get("/jobs/{job_id}", dependencies=[Depends(require_owner)])
async def job_detail(job_id: UUID, db: DB) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ConsultantGeneration, Article.working_title)
            .join(Article, Article.id == ConsultantGeneration.article_id)
            .where(ConsultantGeneration.id == job_id, Article.experience == "consultant")
        )
    ).first()
    if row is None:
        raise AppError(status_code=404, code="not_found", message="Job not found")
    job, title = row
    events = (
        await db.scalars(
            select(ConsultantGenerationEvent)
            .where(ConsultantGenerationEvent.generation_id == job_id)
            .order_by(ConsultantGenerationEvent.started_at, ConsultantGenerationEvent.id)
        )
    ).all()
    expired = bool(
        job.state == "running" and job.lease_until and job.lease_until < datetime.now(UTC)
    )
    return {
        **summarize(job, title, events),
        "events": [serialize_event(event, expired) for event in events],
    }
