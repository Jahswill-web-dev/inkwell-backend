"""Durable consultant worker. Run: python -m app.workers.consultant"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from openai import APIConnectionError, APIStatusError, APITimeoutError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models.article import Article
from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import (
    ConsultantDraftRevision,
    ConsultantGeneration,
    ConsultantInterview,
    ConsultantProfile,
)
from app.db.session import create_engine, create_session_factory
from app.schemas.consultant import Evidence, GeneratedDraft
from app.services.consultant_writer import (
    ConsultantWriter,
    editor_sections,
    validate_draft,
    validate_evidence,
)

log = logging.getLogger(__name__)
LEASE_SECONDS = 180


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
        writer = ConsultantWriter(settings, model)
        writer.usage.update(previous_usage or {})
        evidence = (
            Evidence.model_validate(evidence_data)
            if evidence_data
            else await writer.understand(snapshot)
        )
        validate_evidence(evidence, snapshot["turns"])
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
            else await writer.write(snapshot, evidence.model_dump())
        )
        await checkpoint(
            factory,
            job_id,
            token,
            candidate=candidate.model_dump(),
            stage="check",
            usage=writer.usage,
        )
        issues = validate_draft(candidate, snapshot["turns"])
        check = await writer.check(snapshot, candidate)
        if issues or check.serious_issues or check.style_issues:
            candidate = await writer.parse(
                GeneratedDraft,
                "Repair only the listed issues while preserving supported meaning and voice.",
                {
                    **snapshot,
                    "candidate": candidate.model_dump(),
                    "issues": issues + check.serious_issues + check.style_issues,
                },
            )
            await checkpoint(factory, job_id, token, candidate=candidate.model_dump())
            check = await writer.check(snapshot, candidate)
            if validate_draft(candidate, snapshot["turns"]) or check.serious_issues:
                raise ValueError("The draft could not pass source fidelity checks")
        sections = editor_sections(candidate)
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
            # Initial generation may create the draft. Every subsequent run is a proposal.
            if draft is None and snapshot.get("base_sections") is None:
                db.add(ArticleDraft(article_id=article.id, sections=sections))
            revision = ConsultantDraftRevision(
                article_id=article.id,
                generation_id=job.id,
                kind="generated" if draft is None else "proposal",
                sections=sections,
                gaps=candidate.gaps,
            )
            db.add(revision)
            p = await db.scalar(
                select(ConsultantProfile)
                .where(ConsultantProfile.user_id == article.user_id)
                .with_for_update()
            )
            if p:
                p.suggested_voice = evidence.voice.model_dump()
            article.status = "in_review"
            job.state, job.stage, job.error = "succeeded", "complete", None
            job.usage = writer.usage
            job.lease_until, job.lease_token = None, None
        log.info("consultant_generation_completed job_id=%s model=%s", job_id, model)
    except Exception as exc:
        transient = isinstance(exc, (APIConnectionError, APITimeoutError)) or (
            isinstance(exc, APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500)
        )
        async with factory.begin() as db:
            job = await db.scalar(
                select(ConsultantGeneration)
                .where(ConsultantGeneration.id == job_id)
                .with_for_update()
            )
            if job and job.lease_token == token:
                job.state = "queued" if transient and job.attempts < 3 else "failed"
                job.error = "We couldn't finish this draft. Your interview is saved. Please retry."
                job.lease_until = (
                    datetime.now(UTC) + timedelta(seconds=15 * job.attempts)
                    if job.state == "queued"
                    else None
                )
                job.lease_token = None
                if writer:
                    job.usage = writer.usage
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
