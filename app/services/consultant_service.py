from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import (
    ConsultantDraftFinding,
    ConsultantDraftRevision,
    ConsultantGeneration,
    ConsultantGenerationEvent,
    ConsultantInterview,
    ConsultantProfile,
)
from app.db.models.interview_transcript import InterviewTranscript
from app.db.models.user import User
from app.db.models.workspace import Workspace, WorkspaceMember
from app.prompts.consultant import PROMPT_VERSION
from app.services.consultant_findings import ensure_editor_anchors, locate_paragraph

COVERAGE_GOALS = [
    "The article's central argument",
    "The intended reader and the problem they face",
    "The consultant's distinctive viewpoint",
    "A concrete personal or client experience",
    "The practical method or process",
    "Important limitations, risks, or qualifications",
    "The practical takeaway for the reader",
]

TYPED_QUESTIONS = [
    "What is the main thing you want readers to understand?",
    "Where do people usually get stuck with this?",
    "What do you see differently from the usual advice?",
    "Can you walk me through a specific example?",
    "How do you approach this with a client?",
    "What should someone do differently after reading this?",
]


def fail(code: str, message: str, status: int = 409) -> AppError:
    return AppError(status_code=status, code=code, message=message)


async def profile(session: AsyncSession, user: User) -> ConsultantProfile:
    # Serialize creation and voice/profile mutations per owner.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    record = await session.scalar(
        select(ConsultantProfile).where(ConsultantProfile.user_id == user.id)
    )
    if record is None:
        record = ConsultantProfile(user_id=user.id, display_name=user.username)
        session.add(record)
        await session.flush()
    return record


async def experience_workspace(
    session: AsyncSession, user: User, experience: str, settings: Settings
) -> Workspace:
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    query = (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.experience == experience)
    )
    if experience == "consultant":
        query = query.where(Workspace.personal_owner_id == user.id)
    workspace = await session.scalar(
        query.order_by(WorkspaceMember.is_default.desc(), Workspace.created_at).limit(1)
    )
    if workspace is None:
        if experience == "consultant" and not settings.consultant_enabled:
            raise fail("consultant_disabled", "New consultant accounts are not available yet.", 503)
        workspace = Workspace(
            name=f"{user.username}'s {experience} workspace",
            experience=experience,
            personal_owner_id=user.id if experience == "consultant" else None,
        )
        session.add(workspace)
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id, user_id=user.id, role="owner", is_default=False
            )
        )
        await session.flush()
    return workspace


async def select_experience(
    session: AsyncSession, user: User, experience: str, settings: Settings
) -> None:
    workspace = await experience_workspace(session, user, experience, settings)
    await session.execute(
        update(WorkspaceMember).where(WorkspaceMember.user_id == user.id).values(is_default=False)
    )
    await session.flush()
    await session.execute(
        update(WorkspaceMember)
        .where(WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == workspace.id)
        .values(is_default=True)
    )
    user.preferred_experience = experience


async def owned_article(
    session: AsyncSession, user_id: UUID, article_id: UUID, lock: bool = False
) -> Article:
    query = select(Article).where(
        Article.id == article_id, Article.user_id == user_id, Article.experience == "consultant"
    )
    if lock:
        query = query.with_for_update()
    article = await session.scalar(query)
    if article is None:
        raise fail("article_not_found", "Article not found.", 404)
    return article


async def owned_interview(
    session: AsyncSession, user_id: UUID, interview_id: UUID, lock: bool = False
) -> ConsultantInterview:
    query = select(ConsultantInterview).where(
        ConsultantInterview.id == interview_id, ConsultantInterview.user_id == user_id
    )
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise fail("interview_not_found", "Interview not found.", 404)
    return record


async def transcript(session: AsyncSession, interview: ConsultantInterview) -> InterviewTranscript:
    record = await session.scalar(
        select(InterviewTranscript).where(
            InterviewTranscript.consultant_interview_id == interview.id
        )
    )
    if record is None:
        record = InterviewTranscript(consultant_interview_id=interview.id, turns=[])
        session.add(record)
        await session.flush()
    return record


def effective_turns(
    turns: list[dict[str, Any]], corrections: dict[str, Any]
) -> list[dict[str, Any]]:
    result = []
    for original in turns:
        correction = corrections.get(original["item_id"], {})
        if correction.get("excluded"):
            continue
        turn = {key: value for key, value in original.items() if key != "complete"}
        if correction.get("text") is not None:
            turn["text"] = correction["text"]
        if turn["text"].strip() and turn["text"] != "[Question skipped]":
            result.append(turn)
    return result


async def enqueue(
    session: AsyncSession,
    user: User,
    interview: ConsultantInterview,
    settings: Settings,
    request_key: str,
    instruction: str = "initial",
    request_kind: str = "generation",
) -> ConsultantGeneration:
    article = await owned_article(session, user.id, interview.article_id, lock=True)
    existing = await session.scalar(
        select(ConsultantGeneration).where(
            ConsultantGeneration.article_id == article.id,
            ConsultantGeneration.request_key == request_key,
        )
    )
    if existing:
        return existing
    running = await session.scalar(
        select(ConsultantGeneration.id).where(
            ConsultantGeneration.article_id == article.id,
            ConsultantGeneration.state.in_(["queued", "running"]),
        )
    )
    if running:
        raise fail("generation_running", "A draft is already being created. Please wait.")
    records = (
        await session.scalars(
            select(ConsultantInterview)
            .where(ConsultantInterview.article_id == article.id)
            .order_by(ConsultantInterview.created_at)
        )
    ).all()
    turns: list[dict[str, Any]] = []
    revisions: dict[str, int] = {}
    for record in records:
        source = await transcript(session, record)
        # Namespace provider IDs across replacement sessions/interviews.
        for turn in effective_turns(source.turns, record.corrections):
            turn["item_id"] = f"{record.id}:{turn['item_id']}"
            turns.append(turn)
        revisions[str(record.id)] = record.revision
    if not any(t["speaker"] == "participant" and len(t["text"].strip()) >= 10 for t in turns):
        raise fail(
            "interview_empty", "Add a little more about your idea before creating a draft.", 422
        )
    if sum(len(t["text"]) for t in turns) > 300000:
        raise fail(
            "interview_too_long",
            "This interview is too long to draft safely. Exclude unrelated passages first.",
            422,
        )
    current = await session.scalar(
        select(ArticleDraft).where(ArticleDraft.article_id == article.id)
    )
    snapshot = {
        "article": {
            "title": article.working_title,
            "description": article.notes,
            "audience": article.target_audience,
            "takeaway": article.call_to_action,
            "length": article.target_length,
        },
        "turns": turns,
        "transcript_revisions": revisions,
        "instruction": instruction,
        "request_kind": request_kind,
        "base_sections": current.sections if current else None,
        "base_draft_updated_at": current.updated_at.isoformat() if current else None,
    }
    job = ConsultantGeneration(
        article_id=article.id,
        interview_id=interview.id,
        request_key=request_key,
        snapshot=snapshot,
        model_id=settings.consultant_model,
        prompt_version=PROMPT_VERSION,
        stage="plan" if request_kind == "ask_ai" else "write",
    )
    session.add(job)
    article.status = "ready_to_draft"
    await session.flush()
    session.add(
        ConsultantGenerationEvent(
            generation_id=job.id,
            attempt=0,
            kind="queue",
            started_at=job.created_at or datetime.now(UTC),
            outcome="running",
        )
    )
    return job


def job_response(job: ConsultantGeneration) -> dict[str, Any]:
    candidate = job.candidate or {}
    suggestions = []
    if job.state == "succeeded" and job.snapshot.get("base_sections") is None:
        seen: set[tuple[str, str]] = set()
        sections = candidate.get("sections", [])
        for finding in candidate.get("review_suggestions", []):
            index = finding.get("section_index", -1)
            classification = finding.get("classification") or (
                "interpretation" if finding.get("severity") == "style" else "unsupported"
            )
            if classification != "interpretation" or not 0 <= index < len(sections):
                continue
            heading = sections[index].get("heading", "Article")
            explanation = finding.get("explanation", "").strip()
            key = (heading, explanation)
            if explanation and key not in seen:
                suggestions.append({"section": heading, "text": explanation})
                seen.add(key)
            if len(suggestions) == 3:
                break
    return {
        "id": job.id,
        "state": job.state,
        "stage": job.stage,
        "error": job.error,
        "attempts": job.attempts,
        "created_at": job.created_at,
        "instruction": job.snapshot.get("instruction", ""),
        "request_kind": job.snapshot.get("request_kind", "generation"),
        "writing_suggestions": suggestions,
        "clarification_question": (
            candidate.get("clarification_question") if job.state == "needs_clarification" else None
        ),
        "review_status": (
            "needs_attention" if candidate.get("open_finding_count", 0) else "clear"
        ) if job.state == "succeeded" else None,
        "open_finding_count": candidate.get("open_finding_count", 0),
    }


async def save_draft(
    session: AsyncSession, article: Article, sections: list[dict[str, Any]]
) -> None:
    sections = ensure_editor_anchors(sections)
    draft = await session.scalar(select(ArticleDraft).where(ArticleDraft.article_id == article.id))
    if draft is None:
        draft = ArticleDraft(article_id=article.id, sections=sections)
        session.add(draft)
    elif draft.sections != sections:
        draft.sections = sections
    await session.flush()
    findings = (
        await session.scalars(
            select(ConsultantDraftFinding).where(
                ConsultantDraftFinding.draft_id == draft.id,
                ConsultantDraftFinding.status == "open",
            )
        )
    ).all()
    for finding in findings:
        try:
            locate_paragraph(
                sections,
                finding.section_id,
                finding.paragraph_id,
                finding.section_index,
                finding.paragraph_index,
            )
        except LookupError:
            finding.status = "resolved"
            finding.resolution_reason = "content_removed"
    await session.execute(
        delete(ConsultantDraftRevision).where(ConsultantDraftRevision.article_id == article.id)
    )
    article.status = "in_review"
    await session.flush()
