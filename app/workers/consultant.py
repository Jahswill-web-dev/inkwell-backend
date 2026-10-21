"""Durable consultant worker. Run: python -m app.workers.consultant"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any
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
from app.schemas.consultant import DraftFinding, Evidence, GeneratedDraft
from app.services.consultant_ask_ai import (
    AskAIAccuracyError,
    AskAIClarification,
    prepare_ask_ai,
)
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
    sanitize_draft_quotes,
    sanitize_evidence,
    validate_draft,
    validate_draft_findings,
    validate_evidence,
)

log = logging.getLogger(__name__)
LEASE_SECONDS = 180
ACTIVE_ATTEMPT_CLOCKS: dict[str, float] = {}
ACTIVE_STAGE_CLOCKS: dict[str, float] = {}


class DraftSourceFidelityError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("The draft could not pass source fidelity checks")
        self.issues = issues


def source_fidelity_message(issues: list[str]) -> str:
    """Show bounded checker feedback to the article owner without logging draft text."""
    cleaned = list(
        dict.fromkeys(re.sub(r"\s+", " ", issue).strip()[:280] for issue in issues if issue.strip())
    )[:4]
    if not cleaned:
        return (
            "We couldn't verify this draft against your interview. "
            "Your interview is saved. Please retry."
        )
    details = "\n".join(f"{index}. {issue}" for index, issue in enumerate(cleaned, 1))
    return (
        "We couldn't verify this draft against your interview. The draft checker flagged:\n"
        f"{details}\nYour interview is saved. Please review these points before retrying."
    )


def record_stage(usage: dict[str, object], stage: str, started: float) -> None:
    durations = usage.setdefault("stage_seconds", {})
    assert isinstance(durations, dict)
    durations[stage] = round(float(durations.get(stage, 0)) + time.perf_counter() - started, 3)


def finding_message(finding: DraftFinding, draft: GeneratedDraft) -> str:
    heading = draft.sections[finding.section_index].heading
    location = f'Section "{heading}"'
    if finding.paragraph_index is not None:
        location += f", paragraph {finding.paragraph_index + 1}"
    return f"{location}: {finding.explanation}"


def validate_finding_locations(findings: list[DraftFinding], draft: GeneratedDraft) -> None:
    for finding in findings:
        if finding.section_index >= len(draft.sections):
            raise DraftSourceFidelityError(["Review could not identify the affected section."])
        if finding.paragraph_index is not None and finding.paragraph_index >= len(
            draft.sections[finding.section_index].paragraphs
        ):
            raise DraftSourceFidelityError(["Review could not identify the affected paragraph."])


async def review_initial_draft(
    writer: ConsultantWriter, snapshot: dict[str, Any], candidate: GeneratedDraft
) -> tuple[GeneratedDraft, list[DraftFinding]]:
    """Review once in full; repair only sections with accuracy findings."""
    metrics = getattr(writer, "metrics", None)
    validation = await metrics.start("stage", "source_validation") if metrics else None
    try:
        local_findings = validate_draft_findings(
            candidate, snapshot["turns"], snapshot.get("external_research")
        )
    except Exception:
        if validation:
            await metrics.finish(*validation, "failed")
        raise
    if validation:
        await metrics.finish(*validation, "completed")
    started = time.perf_counter()
    full_check = await writer.check(snapshot, candidate)
    record_stage(writer.usage, "check_full", started)
    validate_finding_locations(full_check.findings, candidate)
    findings = local_findings + full_check.findings
    serious = [finding for finding in findings if finding.severity == "serious"]
    style = [finding for finding in findings if finding.severity == "style"]
    if not serious:
        return candidate, style

    indices = {finding.section_index for finding in serious}
    started = time.perf_counter()
    repairs = await writer.repair_sections(snapshot, candidate, serious)
    record_stage(writer.usage, "repair_sections", started)
    returned = [repair.section_index for repair in repairs.repairs]
    if len(returned) != len(indices) or set(returned) != indices:
        raise DraftSourceFidelityError(["Repair did not return every affected section."])
    sections = list(candidate.sections)
    for repair in repairs.repairs:
        sections[repair.section_index] = repair.section
    repaired = candidate.model_copy(update={"sections": sections})
    repaired = sanitize_draft_quotes(repaired, snapshot["turns"])
    validation = await metrics.start("stage", "source_validation") if metrics else None
    try:
        local_recheck = validate_draft_findings(
            repaired, snapshot["turns"], snapshot.get("external_research")
        )
    except Exception:
        if validation:
            await metrics.finish(*validation, "failed")
        raise
    if validation:
        await metrics.finish(*validation, "completed")
    started = time.perf_counter()
    section_check = await writer.check_repaired_sections(snapshot, repaired, indices)
    record_stage(writer.usage, "check_sections", started)
    validate_finding_locations(section_check.findings, repaired)
    remaining = [
        finding
        for finding in local_recheck + section_check.findings
        if finding.severity == "serious"
    ]
    if remaining:
        raise DraftSourceFidelityError(
            [finding_message(finding, repaired) for finding in remaining]
        )
    style = [finding for finding in style if finding.section_index not in indices]
    style.extend(
        finding for finding in local_recheck + section_check.findings if finding.severity == "style"
    )
    return repaired, style


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
            evidence_data, candidate_data = job.evidence, job.candidate
            queued_at = job.created_at
        model_snapshot = compact_snapshot(snapshot)
        writer = ConsultantWriter(settings, model)
        writer.metrics = MetricsRecorder(factory, job_id, job.attempts, token)
        writer.usage.update(previous_usage or {})
        writer.usage["queue_seconds"] = round(
            max(0, (datetime.now(UTC) - queued_at).total_seconds()), 3
        )
        started = time.perf_counter()
        evidence = (
            Evidence.model_validate(evidence_data)
            if evidence_data
            else await writer.understand(model_snapshot)
        )
        if not evidence_data:
            record_stage(writer.usage, "understand", started)
        await checkpoint(factory, job_id, token, stage="validation", usage=writer.usage)
        evidence = sanitize_evidence(evidence, model_snapshot["turns"])
        validate_evidence(evidence, model_snapshot["turns"])
        if snapshot.get("request_kind") == "ask_ai":

            async def progress(stage: str) -> None:
                await checkpoint(factory, job_id, token, stage=stage, usage=writer.usage)

            sections, candidate_data, ai_snapshot = await prepare_ask_ai(
                writer,
                {**snapshot, "turns": model_snapshot["turns"]},
                evidence,
                progress,
            )
            if ai_snapshot.get("external_research"):
                snapshot = {**snapshot, "external_research": ai_snapshot["external_research"]}
            await checkpoint(
                factory,
                job_id,
                token,
                snapshot=snapshot,
                evidence=evidence.model_dump(),
                candidate=candidate_data,
                usage=writer.usage,
            )
            writing_suggestions = []
        else:
            needs_research = bool(
                re.search(
                    r"\b(source|sources|citations?|research|stud(?:y|ies)|statistics?|"
                    r"real.world|case stud(?:y|ies)|public example)\b",
                    str(snapshot.get("instruction", "")),
                    re.IGNORECASE,
                )
            )
            if needs_research and "external_research" not in snapshot:
                await checkpoint(factory, job_id, token, stage="research", usage=writer.usage)
                research = await writer.research(model_snapshot)
                snapshot = {**snapshot, "external_research": research}
                model_snapshot["external_research"] = research
                await checkpoint(factory, job_id, token, snapshot=snapshot, usage=writer.usage)
            elif snapshot.get("external_research"):
                model_snapshot["external_research"] = snapshot["external_research"]
            await checkpoint(
                factory,
                job_id,
                token,
                evidence=evidence.model_dump(),
                stage="write",
                usage=writer.usage,
            )
            started = time.perf_counter()
            candidate = (
                GeneratedDraft.model_validate(candidate_data)
                if candidate_data
                else await writer.write(model_snapshot, evidence.model_dump())
            )
            if not candidate_data:
                record_stage(writer.usage, "write", started)
            candidate = sanitize_draft_quotes(candidate, model_snapshot["turns"])
            await checkpoint(
                factory,
                job_id,
                token,
                candidate=candidate.model_dump(),
                stage="check",
                usage=writer.usage,
            )
            writing_suggestions: list[DraftFinding] = []
            if snapshot.get("base_sections") is None:
                candidate, writing_suggestions = await review_initial_draft(
                    writer,
                    {**model_snapshot, "evidence": {"argument": evidence.argument}},
                    candidate,
                )
            else:
                issues = validate_draft(
                    candidate, model_snapshot["turns"], model_snapshot.get("external_research")
                )
                check = await writer.check(model_snapshot, candidate)
                if issues or check.serious_issues or check.style_issues:
                    candidate = await writer.parse(
                        GeneratedDraft,
                        "Repair listed issues while preserving supported meaning and voice. "
                        "If a claim, instruction, example, or quotation is unsupported, "
                        "remove it or use cited participant turns or verified public research. "
                        "Never retain unsupported advice merely to keep the article's length.",
                        {
                            **model_snapshot,
                            "candidate": candidate.model_dump(),
                            "issues": issues + check.serious_issues + check.style_issues,
                        },
                    )
                    candidate = sanitize_draft_quotes(candidate, model_snapshot["turns"])
                    await checkpoint(factory, job_id, token, candidate=candidate.model_dump())
                    check = await writer.check(model_snapshot, candidate)
                    remaining_issues = validate_draft(
                        candidate,
                        model_snapshot["turns"],
                        model_snapshot.get("external_research"),
                    )
                    if remaining_issues or check.serious_issues:
                        raise DraftSourceFidelityError(remaining_issues + check.serious_issues)
            candidate_data = candidate.model_dump()
            if writing_suggestions:
                candidate_data["review_suggestions"] = [
                    finding.model_dump() for finding in writing_suggestions
                ]
            await checkpoint(factory, job_id, token, candidate=candidate_data, usage=writer.usage)
            sections = editor_sections(
                candidate, model_snapshot.get("external_research", {}).get("sources", [])
            )
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
                db.add(ArticleDraft(article_id=article.id, sections=sections))
            else:
                db.add(
                    ConsultantDraftRevision(
                        article_id=article.id,
                        generation_id=job.id,
                        kind="proposal",
                        sections=sections,
                        gaps=candidate_data.get("gaps", []),
                    )
                )
            article.status = "in_review"
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
                    job.error = source_fidelity_message(exc.issues)
                elif rate_limited:
                    job.error = (
                        "Drafting is temporarily unavailable because the AI service rate limit "
                        "was reached. Your interview is safe. Please retry shortly."
                    )
                elif isinstance(exc, DraftSourceFidelityError):
                    job.error = source_fidelity_message(exc.issues)
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
