"""Durable consultant worker. Run: python -m app.workers.consultant"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models.article import Article
from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import (
    ConsultantDraftRevision,
    ConsultantGeneration,
    ConsultantGenerationEvent,
    ConsultantInterview,
)
from app.db.session import create_engine, create_session_factory
from app.schemas.consultant import GeneratedDraft
from app.services.consultant_ask_ai import (
    AskAIAccuracyError,
    AskAIClarification,
    prepare_ask_ai,
)
from app.services.consultant_findings import ensure_editor_anchors
from app.services.consultant_metrics import (
    MetricsRecorder,
    close_event,
    close_open_events,
    elapsed_ms,
    open_event,
)
from app.services.consultant_writer import (
    ConsultantWriter,
    ModelOutputError,
    compact_snapshot,
    editor_sections,
)

log = logging.getLogger(__name__)
LEASE_SECONDS = 180
ACTIVE_ATTEMPT_CLOCKS: dict[str, float] = {}
ACTIVE_STAGE_CLOCKS: dict[str, float] = {}


def record_stage(usage: dict[str, object], stage: str, started: float) -> None:
    durations = usage.setdefault("stage_seconds", {})
    assert isinstance(durations, dict)
    durations[stage] = round(float(durations.get(stage, 0)) + time.perf_counter() - started, 3)


async def claim(factory: async_sessionmaker[AsyncSession]) -> tuple[UUID, str] | None:
    async with factory.begin() as db:
        now = datetime.now(UTC)
        job = await db.scalar(
            select(ConsultantGeneration)
            .where(
                or_(
                    and_(
                        ConsultantGeneration.state == "queued",
                        or_(
                            ConsultantGeneration.lease_until.is_(None),
                            ConsultantGeneration.lease_until < now,
                        ),
                    ),
                    and_(
                        ConsultantGeneration.state == "running",
                        ConsultantGeneration.lease_until < now,
                    ),
                )
            )
            .order_by(ConsultantGeneration.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.attempts >= 3:
            previous_state = job.state
            job.state, job.error = (
                "failed",
                "Generation was interrupted repeatedly. Your interview is safe; retry when ready.",
            )
            await close_open_events(
                db,
                job.id,
                job.attempts,
                "interrupted" if previous_state == "running" else "completed",
                job.lease_until if previous_state == "running" else now,
            )
            outcome = await open_event(
                db, job.id, job.attempts, "outcome", "failed", started_at=now
            )
            close_event(outcome, outcome="failed", end=now)
            job.lease_until, job.lease_token = None, None
            await db.execute(
                update(Article).where(Article.id == job.article_id).values(status="ready_to_draft")
            )
            return None
        if job.state == "running":
            if job.lease_token:
                ACTIVE_ATTEMPT_CLOCKS.pop(job.lease_token, None)
                ACTIVE_STAGE_CLOCKS.pop(job.lease_token, None)
            interrupted_at = job.lease_until or now
            await close_open_events(db, job.id, job.attempts, "interrupted", interrupted_at)
            gap = await open_event(
                db, job.id, job.attempts, "queue", "recovery", started_at=interrupted_at
            )
            close_event(gap, outcome="completed", end=now)
        else:
            queued = await db.scalar(
                select(ConsultantGenerationEvent)
                .where(
                    ConsultantGenerationEvent.generation_id == job.id,
                    ConsultantGenerationEvent.kind == "queue",
                    ConsultantGenerationEvent.ended_at.is_(None),
                )
                .with_for_update()
            )
            if queued is None:
                queued = await open_event(
                    db, job.id, job.attempts, "queue", started_at=job.created_at
                )
            close_event(queued, outcome="completed", end=now)
        job.state, job.attempts = "running", job.attempts + 1
        job.lease_token = str(uuid4())
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        await open_event(db, job.id, job.attempts, "attempt", started_at=now)
        await open_event(db, job.id, job.attempts, "stage", job.stage, started_at=now)
        ACTIVE_ATTEMPT_CLOCKS[job.lease_token] = time.perf_counter()
        ACTIVE_STAGE_CLOCKS[job.lease_token] = time.perf_counter()
        await db.execute(
            update(Article).where(Article.id == job.article_id).values(status="drafting")
        )
        return job.id, job.lease_token


async def heartbeat(factory: async_sessionmaker[AsyncSession], job_id: UUID, token: str) -> None:
    while True:
        await asyncio.sleep(30)
        async with factory.begin() as db:
            await db.execute(
                update(ConsultantGeneration)
                .where(
                    ConsultantGeneration.id == job_id,
                    ConsultantGeneration.lease_token == token,
                    ConsultantGeneration.state == "running",
                )
                .values(lease_until=datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS))
            )


async def checkpoint(
    factory: async_sessionmaker[AsyncSession], job_id: UUID, token: str, **values: object
) -> None:
    async with factory.begin() as db:
        job = await db.scalar(
            select(ConsultantGeneration).where(ConsultantGeneration.id == job_id).with_for_update()
        )
        if job is None or job.lease_token != token or job.state != "running":
            raise RuntimeError("Generation lease lost")
        if "stage" in values and values["stage"] != job.stage:
            current = await db.scalar(
                select(ConsultantGenerationEvent)
                .where(
                    ConsultantGenerationEvent.generation_id == job_id,
                    ConsultantGenerationEvent.attempt == job.attempts,
                    ConsultantGenerationEvent.kind == "stage",
                    ConsultantGenerationEvent.ended_at.is_(None),
                )
                .with_for_update()
            )
            if current:
                clock = ACTIVE_STAGE_CLOCKS.get(token)
                close_event(
                    current,
                    outcome="completed",
                    duration_ms=elapsed_ms(clock) if clock is not None else None,
                )
            await open_event(db, job_id, job.attempts, "stage", str(values["stage"]))
            ACTIVE_STAGE_CLOCKS[token] = time.perf_counter()
        for key, value in values.items():
            setattr(job, key, value)


async def process(factory: async_sessionmaker[AsyncSession], job_id: UUID, token: str) -> None:
    settings = get_settings()
    process_started = time.perf_counter()
    pulse = asyncio.create_task(heartbeat(factory, job_id, token))
    writer = None
    try:
        async with factory() as db:
            job = await db.get(ConsultantGeneration, job_id)
            assert job is not None
            snapshot, model = job.snapshot, job.model_id
            previous_usage = job.usage
            candidate_data = job.candidate
            queued_at = job.created_at
        model_snapshot = compact_snapshot(snapshot)
        writer = ConsultantWriter(settings, model)
        writer.metrics = MetricsRecorder(factory, job_id, job.attempts, token)
        writer.usage.update(previous_usage or {})
        writer.usage["queue_seconds"] = round(
            max(0, (datetime.now(UTC) - queued_at).total_seconds()), 3
        )
        if snapshot.get("request_kind") == "ask_ai":

            async def progress(stage: str) -> None:
                await checkpoint(factory, job_id, token, stage=stage, usage=writer.usage)

            sections, candidate_data, ai_snapshot = await prepare_ask_ai(
                writer,
                {**snapshot, "turns": model_snapshot["turns"]},
                progress,
            )
            if ai_snapshot.get("external_research"):
                snapshot = {**snapshot, "external_research": ai_snapshot["external_research"]}
            await checkpoint(
                factory,
                job_id,
                token,
                snapshot=snapshot,
                candidate=candidate_data,
                usage=writer.usage,
            )
        else:
            await checkpoint(
                factory,
                job_id,
                token,
                stage="write",
                usage=writer.usage,
            )
            started = time.perf_counter()
            candidate = (
                GeneratedDraft.model_validate(candidate_data)
                if candidate_data
                else await writer.write(model_snapshot)
            )
            if not candidate_data:
                record_stage(writer.usage, "write", started)
            await checkpoint(
                factory,
                job_id,
                token,
                candidate=candidate.model_dump(mode="json"),
                stage="saving",
                usage=writer.usage,
            )
            candidate_data = candidate.model_dump(mode="json")
            await checkpoint(factory, job_id, token, candidate=candidate_data, usage=writer.usage)
            sections = editor_sections(
                candidate, model_snapshot.get("external_research", {}).get("sources", [])
            )
        sections = ensure_editor_anchors(sections)
        await checkpoint(factory, job_id, token, stage="saving", usage=writer.usage)
        async with factory.begin() as db:
            job = await db.scalar(
                select(ConsultantGeneration)
                .where(ConsultantGeneration.id == job_id)
                .with_for_update()
            )
            if job is None or job.lease_token != token or job.state != "running":
                return
            article = await db.scalar(
                select(Article).where(Article.id == job.article_id).with_for_update()
            )
            if article is None:
                return
            interviews = (
                await db.scalars(
                    select(ConsultantInterview).where(ConsultantInterview.article_id == article.id)
                )
            ).all()
            if any(
                snapshot["transcript_revisions"].get(str(i.id)) != i.revision for i in interviews
            ):
                raise ValueError("Transcript changed during generation; create an updated proposal")
            draft = await db.scalar(
                select(ArticleDraft).where(ArticleDraft.article_id == article.id)
            )
            base_version = snapshot.get("base_draft_updated_at")
            if base_version and (draft is None or draft.updated_at.isoformat() != base_version):
                raise ValueError("The draft changed while this suggestion was generated")
            # Keep one active draft and at most one pending rewrite proposal.
            await db.execute(
                delete(ConsultantDraftRevision).where(
                    ConsultantDraftRevision.article_id == article.id
                )
            )
            if draft is None and snapshot.get("base_sections") is None:
                draft = ArticleDraft(article_id=article.id, sections=sections)
                db.add(draft)
                await db.flush()
            else:
                revision = ConsultantDraftRevision(
                    article_id=article.id,
                    generation_id=job.id,
                    kind="proposal",
                    sections=sections,
                    gaps=candidate_data.get("gaps", []),
                )
                db.add(revision)
                await db.flush()
            candidate_data["findings"] = []
            candidate_data["review_suggestions"] = []
            candidate_data["open_finding_count"] = 0
            article.status = "ready_to_publish"
            job.state, job.stage, job.error = "succeeded", "complete", None
            job.candidate = candidate_data
            record_stage(writer.usage, "total", process_started)
            writer.usage["total_wall_seconds"] = round(
                (datetime.now(UTC) - queued_at).total_seconds(), 3
            )
            job.usage = writer.usage
            unfinished = await db.scalar(
                select(ConsultantGenerationEvent.id).where(
                    ConsultantGenerationEvent.generation_id == job_id,
                    ConsultantGenerationEvent.attempt == job.attempts,
                    ConsultantGenerationEvent.kind.in_(["call", "wait"]),
                    ConsultantGenerationEvent.ended_at.is_(None),
                )
            )
            if unfinished is not None:
                raise RuntimeError("Cannot complete generation with unfinished performance events")
            job.lease_until, job.lease_token = None, None
            await close_open_events(
                db,
                job_id,
                job.attempts,
                "completed",
                duration_by_kind={
                    "attempt": elapsed_ms(ACTIVE_ATTEMPT_CLOCKS[token]),
                    "stage": elapsed_ms(ACTIVE_STAGE_CLOCKS[token]),
                },
            )
        log.info("consultant_generation_completed job_id=%s model=%s", job_id, model)
    except Exception as exc:
        rate_limited = isinstance(exc, APIStatusError) and exc.status_code == 429
        transient = isinstance(
            exc, (APIConnectionError, APITimeoutError, ModelOutputError, ValidationError)
        ) or (isinstance(exc, APIStatusError) and (rate_limited or exc.status_code >= 500))
        async with factory.begin() as db:
            job = await db.scalar(
                select(ConsultantGeneration)
                .where(ConsultantGeneration.id == job_id)
                .with_for_update()
            )
            if job and job.lease_token == token:
                job.state = (
                    "needs_clarification"
                    if isinstance(exc, AskAIClarification)
                    else "queued"
                    if transient and job.attempts < 3
                    else "failed"
                )
                if isinstance(exc, AskAIClarification):
                    job.error = None
                    job.candidate = {"clarification_question": exc.question}
                elif isinstance(exc, AskAIAccuracyError):
                    job.error = "The requested rewrite could not be created. Please try again."
                elif rate_limited:
                    job.error = (
                        "Drafting is temporarily unavailable because the AI service rate limit "
                        "was reached. Your interview is safe. Please retry shortly."
                    )
                elif "draft changed while" in str(exc).lower():
                    job.error = (
                        "Your draft changed while this suggestion was generated. Please try again."
                    )
                else:
                    job.error = (
                        "We couldn't finish this draft. Your interview is saved. Please retry."
                    )
                job.lease_until = (
                    datetime.now(UTC) + timedelta(seconds=15 * job.attempts)
                    if job.state == "queued"
                    else None
                )
                job.lease_token = None
                await close_open_events(
                    db,
                    job_id,
                    job.attempts,
                    "completed" if isinstance(exc, AskAIClarification) else "failed",
                    duration_by_kind={
                        "attempt": elapsed_ms(ACTIVE_ATTEMPT_CLOCKS[token]),
                        "stage": elapsed_ms(ACTIVE_STAGE_CLOCKS[token]),
                    }
                    if token in ACTIVE_ATTEMPT_CLOCKS and token in ACTIVE_STAGE_CLOCKS
                    else None,
                )
                if job.state == "queued":
                    await open_event(db, job_id, job.attempts, "queue", "retry")
                if writer:
                    record_stage(writer.usage, "total", process_started)
                    writer.usage["total_wall_seconds"] = round(
                        (datetime.now(UTC) - queued_at).total_seconds(), 3
                    )
                    job.usage = writer.usage
                if job.state in {"failed", "needs_clarification"}:
                    await db.execute(
                        update(Article)
                        .where(Article.id == job.article_id)
                        .values(status="ready_to_draft")
                    )
        # Never log provider request bodies or transcript-bearing exception text.
        log.warning(
            "consultant_generation_failed job_id=%s error_type=%s", job_id, type(exc).__name__
        )
    finally:
        ACTIVE_ATTEMPT_CLOCKS.pop(token, None)
        ACTIVE_STAGE_CLOCKS.pop(token, None)
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        if writer:
            await writer.client.close()


async def main() -> None:
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)
    try:
        while True:
            try:
                claimed = await claim(factory)
                if claimed:
                    await process(factory, *claimed)
                else:
                    await asyncio.sleep(2)
            except SQLAlchemyError:
                log.warning("consultant_worker_database_unavailable")
                await asyncio.sleep(5)
    finally:
        await engine.dispose()


def run() -> None:
    """Run the worker with an event loop supported by async Psycopg."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
