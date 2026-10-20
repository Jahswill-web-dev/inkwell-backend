"""Durable consultant worker. Run: python -m app.workers.consultant"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
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
    ConsultantInterview,
)
from app.db.session import create_engine, create_session_factory
from app.schemas.consultant import Evidence, GeneratedDraft
from app.services.consultant_writer import (
    ConsultantWriter,
    ModelOutputError,
    compact_snapshot,
    editor_sections,
    sanitize_draft_quotes,
    sanitize_evidence,
    validate_draft,
    validate_evidence,
)

log = logging.getLogger(__name__)
LEASE_SECONDS = 180


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
            job.state, job.error = (
                "failed",
                "Generation was interrupted repeatedly. Your interview is safe; retry when ready.",
            )
            return None
        job.state, job.attempts = "running", job.attempts + 1
        job.lease_token = str(uuid4())
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
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
        for key, value in values.items():
            setattr(job, key, value)


async def process(factory: async_sessionmaker[AsyncSession], job_id: UUID, token: str) -> None:
    settings = get_settings()
    pulse = asyncio.create_task(heartbeat(factory, job_id, token))
    writer = None
    try:
        async with factory() as db:
            job = await db.get(ConsultantGeneration, job_id)
            assert job is not None
            snapshot, model = job.snapshot, job.model_id
            previous_usage = job.usage
            evidence_data, candidate_data = job.evidence, job.candidate
        model_snapshot = compact_snapshot(snapshot)
        writer = ConsultantWriter(settings, model)
        writer.usage.update(previous_usage or {})
        evidence = (
            Evidence.model_validate(evidence_data)
            if evidence_data
            else await writer.understand(model_snapshot)
        )
        evidence = sanitize_evidence(evidence, model_snapshot["turns"])
        validate_evidence(evidence, model_snapshot["turns"])
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
        candidate = (
            GeneratedDraft.model_validate(candidate_data)
            if candidate_data
            else await writer.write(model_snapshot, evidence.model_dump())
        )
        candidate = sanitize_draft_quotes(candidate, model_snapshot["turns"])
        await checkpoint(
            factory,
            job_id,
            token,
            candidate=candidate.model_dump(),
            stage="check",
            usage=writer.usage,
        )
        issues = validate_draft(
            candidate, model_snapshot["turns"], model_snapshot.get("external_research")
        )
        check = await writer.check(model_snapshot, candidate)
        if issues or check.serious_issues or check.style_issues:
            candidate = await writer.parse(
                GeneratedDraft,
                "Repair only the listed issues while preserving supported meaning and voice. "
                "If a claim, instruction, example, or quotation is unsupported, remove it or "
                "rewrite it using only cited participant turns or verified public research. "
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
                candidate, model_snapshot["turns"], model_snapshot.get("external_research")
            )
            if remaining_issues or check.serious_issues:
                raise DraftSourceFidelityError(remaining_issues + check.serious_issues)
        sections = editor_sections(
            candidate, model_snapshot.get("external_research", {}).get("sources", [])
        )
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
                        gaps=candidate.gaps,
                    )
                )
            article.status = "in_review"
            job.state, job.stage, job.error = "succeeded", "complete", None
            job.candidate = candidate.model_dump()
            job.usage = writer.usage
            job.lease_until, job.lease_token = None, None
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
                job.state = "queued" if transient and job.attempts < 3 else "failed"
                if rate_limited:
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
                if writer:
                    job.usage = writer.usage
                if job.state == "failed":
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
