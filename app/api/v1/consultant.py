from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from openai import AsyncOpenAI
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.core.config import get_settings
from app.db.models.article import Article
from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import (
    ConsultantDraftFinding,
    ConsultantDraftRevision,
    ConsultantGeneration,
    ConsultantInterview,
)
from app.db.models.voice_session import VoiceSession
from app.db.models.workspace import Workspace, WorkspaceMember
from app.db.session import get_db_session
from app.prompts.consultant import INTERVIEW_POLICY, source_json
from app.schemas.consultant import (
    ArticleAIRequest,
    ArticleDetailsInput,
    ConsultantArticleInput,
    CorrectionsInput,
    DraftFinding,
    ExperienceInput,
    FindingRepairInput,
    GenerateInput,
    NextQuestion,
    ProfileInput,
    TurnsInput,
    TypedAnswer,
    VoiceCompletionApproval,
)
from app.schemas.draft import ArticleDraftUpdate
from app.schemas.interview_transcript import TranscriptTurn
from app.schemas.realtime import VoiceSessionCreate
from app.services import consultant_service as svc
from app.services.consultant_findings import (
    anchor_findings,
    finding_response,
    locate_paragraph,
    paragraph_text,
    replace_paragraph_text,
)
from app.services.consultant_writer import ConsultantWriter
from app.services.interview_context import ConsultantVoiceContext
from app.services.openai_realtime import OpenAIRealtimeService

router = APIRouter(prefix="/consultant", tags=["consultant"])
DB = Annotated[AsyncSession, Depends(get_db_session)]


def _matches_version(value: str | None, current: datetime) -> bool:
    if not value:
        return False
    try:
        supplied = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return supplied == current


async def _owned_active_finding(
    db: AsyncSession, user: CurrentUser, article_id: UUID, finding_id: UUID, request: Request,
    *, allow_removed: bool = False,
) -> tuple[
    Article, ArticleDraft, ConsultantDraftFinding, dict[str, Any] | None, dict[str, Any] | None
]:
    article = await svc.owned_article(db, user.id, article_id, lock=True)
    draft = await db.scalar(
        select(ArticleDraft).where(ArticleDraft.article_id == article.id).with_for_update()
    )
    if not draft:
        raise svc.fail("draft_not_found", "Draft not found.", 404)
    if not _matches_version(request.headers.get("if-unmodified-since"), draft.updated_at):
        raise svc.fail("draft_conflict", "This draft changed elsewhere. Reload before continuing.")
    finding = await db.scalar(
        select(ConsultantDraftFinding).where(
            ConsultantDraftFinding.id == finding_id,
            ConsultantDraftFinding.article_id == article.id,
            ConsultantDraftFinding.draft_id == draft.id,
        )
    )
    if not finding:
        raise svc.fail("finding_not_found", "Finding not found.", 404)
    try:
        section, paragraph, _, _ = locate_paragraph(
            draft.sections,
            finding.section_id,
            finding.paragraph_id,
            finding.section_index,
            finding.paragraph_index,
        )
    except LookupError as exc:
        finding.status, finding.resolution_reason = "resolved", "content_removed"
        if allow_removed:
            return article, draft, finding, None, None
        raise svc.fail(
            "finding_content_removed", "That paragraph is no longer in the draft."
        ) from exc
    return article, draft, finding, section, paragraph


def _finding_evidence(
    finding: ConsultantDraftFinding, job: ConsultantGeneration | None
) -> dict[str, Any]:
    snapshot = job.snapshot if job else {}
    turn_ids = set(finding.source_turn_ids)
    return {
        "participant_turns": [
            turn
            for turn in snapshot.get("turns", [])
            if turn.get("speaker") == "participant" and turn.get("item_id") in turn_ids
        ],
        "external_research": snapshot.get("external_research"),
    }


async def _focused_review(
    finding: ConsultantDraftFinding,
    before: str,
    after: str,
    section: dict[str, Any],
    db: AsyncSession,
) -> list[DraftFinding]:
    job = (
        await db.get(ConsultantGeneration, finding.generation_id)
        if finding.generation_id
        else None
    )
    settings = get_settings()
    writer = ConsultantWriter(settings, settings.consultant_model)
    result = await writer.check_focused_repair({
        "heading": section.get("title", ""),
        "before": before,
        "replacement_paragraph": after,
        "finding": finding_response(finding),
        **_finding_evidence(finding, job),
    })
    return result.findings


@router.get("/availability")
async def availability(request: Request) -> dict[str, bool]:
    return {"enabled": request.app.state.settings.consultant_enabled}


@router.get("/experience")
async def experience(user: CurrentUser, db: DB) -> dict[str, Any]:
    enabled = (
        await db.scalars(
            select(Workspace.experience)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id)
        )
    ).all()
    return {"experience": user.preferred_experience, "enabled_experiences": sorted(set(enabled))}


@router.put("/experience")
async def switch(
    payload: ExperienceInput, user: CurrentUser, db: DB, request: Request
) -> dict[str, str]:
    await svc.select_experience(db, user, payload.experience, request.app.state.settings)
    return {"experience": payload.experience}


@router.get("/profile")
async def get_profile(user: CurrentUser, db: DB) -> dict[str, Any]:
    p = await svc.profile(db, user)
    return {
        "display_name": p.display_name,
        "specialty": p.specialty,
        "audience": p.audience,
        "onboarding_complete": p.onboarding_complete,
    }


@router.put("/profile")
async def put_profile(
    payload: ProfileInput, user: CurrentUser, db: DB, request: Request
) -> dict[str, bool]:
    await svc.experience_workspace(db, user, "consultant", request.app.state.settings)
    p = await svc.profile(db, user)
    for key, value in payload.model_dump().items():
        setattr(p, key, value)
    p.onboarding_complete = True
    return {"saved": True}


@router.get("/articles")
async def articles(user: CurrentUser, db: DB, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    records = (
        await db.scalars(
            select(Article)
            .where(Article.user_id == user.id, Article.experience == "consultant")
            .order_by(Article.updated_at.desc())
            .offset(max(0, offset))
            .limit(min(100, max(1, limit)))
        )
    ).all()
    return {
        "items": [
            {"id": a.id, "title": a.working_title, "status": a.status, "updated_at": a.updated_at}
            for a in records
        ]
    }


@router.post("/articles", status_code=201)
async def create_article(
    payload: ConsultantArticleInput, user: CurrentUser, db: DB, request: Request
) -> dict[str, Any]:
    workspace = await svc.experience_workspace(db, user, "consultant", request.app.state.settings)
    p = await svc.profile(db, user)
    a = Article(
        user_id=user.id,
        workspace_id=workspace.id,
        experience="consultant",
        working_title=payload.title,
        notes=payload.description,
        target_audience=[payload.audience or p.audience or "Readers interested in this topic"],
        article_goal="educate_with_practical_guidance",
        interview_method="self",
        content_type="blog_post",
        target_length=payload.length,
        interviewee_name=p.display_name,
        call_to_action=payload.takeaway,
    )
    db.add(a)
    await db.flush()
    return {"id": a.id}


@router.get("/articles/{article_id}")
async def article_detail(article_id: UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    a = await svc.owned_article(db, user.id, article_id)
    interviews = (
        await db.scalars(
            select(ConsultantInterview)
            .where(ConsultantInterview.article_id == a.id)
            .order_by(ConsultantInterview.created_at)
        )
    ).all()
    jobs = (
        await db.scalars(
            select(ConsultantGeneration)
            .where(ConsultantGeneration.article_id == a.id)
            .order_by(ConsultantGeneration.created_at.desc())
        )
    ).all()
    revisions = (
        await db.scalars(
            select(ConsultantDraftRevision)
            .where(
                ConsultantDraftRevision.article_id == a.id,
                ConsultantDraftRevision.kind == "proposal",
            )
            .order_by(ConsultantDraftRevision.created_at.desc())
            .limit(1)
        )
    ).all()
    proposal_jobs: dict[UUID, ConsultantGeneration] = {}
    for revision in revisions:
        if revision.generation_id:
            proposal_job = await db.get(ConsultantGeneration, revision.generation_id)
            if proposal_job:
                proposal_jobs[revision.id] = proposal_job
    draft = await db.scalar(select(ArticleDraft).where(ArticleDraft.article_id == a.id))
    return {
        "id": a.id,
        "title": a.working_title,
        "description": a.notes,
        "status": a.status,
        "interviews": [{"id": i.id, "state": i.state} for i in interviews],
        "job": svc.job_response(jobs[0]) if jobs else None,
        "sections": draft.sections if draft else [],
        "draft_updated_at": draft.updated_at if draft else None,
        "findings": [],
        "open_finding_count": 0,
        "revisions": [
            {
                "id": r.id,
                "kind": r.kind,
                "sections": r.sections,
                "gaps": r.gaps,
                "created_at": r.created_at,
                "request": (
                    proposal_jobs[r.id].snapshot.get("instruction", "")
                    if proposal_jobs.get(r.id) else ""
                ),
                "summary": (
                    (proposal_jobs[r.id].candidate or {}).get("summary", "")
                    if proposal_jobs.get(r.id) else ""
                ),
                "sources": (
                    proposal_jobs[r.id].snapshot.get("external_research", {}).get("sources", [])
                    if proposal_jobs.get(r.id) else []
                ),
                "findings": [],
            }
            for r in revisions
        ],
    }


@router.post("/articles/{article_id}/interviews")
async def start_interview(article_id: UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    a = await svc.owned_article(db, user.id, article_id, lock=True)
    running = await db.scalar(
        select(ConsultantGeneration.id).where(
            ConsultantGeneration.article_id == a.id,
            ConsultantGeneration.state.in_(["queued", "running"]),
        )
    )
    if running:
        raise svc.fail(
            "generation_running", "Wait for the current draft before starting a follow-up."
        )
    record = await db.scalar(
        select(ConsultantInterview)
        .where(ConsultantInterview.article_id == a.id, ConsultantInterview.state != "completed")
        .limit(1)
    )
    if record is None:
        record = ConsultantInterview(
            article_id=a.id,
            user_id=user.id,
            questions=[
                {"id": str(index), "text": goal}
                for index, goal in enumerate(svc.COVERAGE_GOALS)
            ],
        )
        db.add(record)
        await db.flush()
    await svc.transcript(db, record)
    a.status = "interview_in_progress"
    return {"id": record.id}


@router.get("/interviews/{interview_id}")
async def get_interview(interview_id: UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    i = await svc.owned_interview(db, user.id, interview_id)
    t = await svc.transcript(db, i)
    a = await svc.owned_article(db, user.id, i.article_id)
    return {
        "id": i.id,
        "article_id": i.article_id,
        "title": a.working_title,
        "description": a.notes,
        "state": i.state,
        "voice_phase": i.voice_phase,
        "questions": i.questions,
        "turns": t.turns,
        "corrections": i.corrections,
    }


@router.post("/interviews/{interview_id}/transcript")
async def append_turns(
    interview_id: UUID, payload: TurnsInput, user: CurrentUser, db: DB
) -> dict[str, Any]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    t = await svc.transcript(db, i)
    existing = {x["item_id"] for x in t.turns}
    new = [x for x in payload.turns if x.item_id not in existing]
    if i.state == "completed" and new:
        raise svc.fail(
            "interview_finalized", "This interview has finished. Start a follow-up to add detail."
        )
    for turn in new:
        if turn.voice_session_id:
            owner = await db.scalar(
                select(VoiceSession.id).where(
                    VoiceSession.id == turn.voice_session_id,
                    VoiceSession.consultant_interview_id == i.id,
                )
            )
            if not owner:
                raise svc.fail(
                    "voice_session_mismatch",
                    "Voice session does not belong to this interview.",
                    422,
                )
        if turn.item_id not in existing:
            t.turns = [*t.turns, turn.model_dump(mode="json", exclude_none=True)]
            existing.add(turn.item_id)
    return {"id": t.id, "saved": True}


@router.put("/interviews/{interview_id}/corrections")
async def corrections(
    interview_id: UUID, payload: CorrectionsInput, user: CurrentUser, db: DB
) -> dict[str, bool]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    await svc.owned_article(db, user.id, i.article_id, lock=True)
    t = await svc.transcript(db, i)
    ids = {turn["item_id"] for turn in t.turns}
    if not set(payload.corrections).issubset(ids):
        raise svc.fail("invalid_turn", "Unknown transcript turn.", 422)
    i.corrections = {
        **i.corrections,
        **{key: value.model_dump() for key, value in payload.corrections.items()},
    }
    i.revision += 1
    return {"saved": True}


@router.post("/interviews/{interview_id}/pause")
async def pause(interview_id: UUID, user: CurrentUser, db: DB) -> dict[str, bool]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    if i.state != "completed":
        i.state = "paused"
        await db.execute(
            update(VoiceSession)
            .where(VoiceSession.consultant_interview_id == i.id, VoiceSession.status == "connected")
            .values(status="paused", ended_at=datetime.now(UTC))
        )
    return {"saved": True}


@router.post("/interviews/{interview_id}/finish")
async def finish(interview_id: UUID, user: CurrentUser, db: DB, request: Request) -> dict[str, Any]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    job = await svc.enqueue(db, user, i, request.app.state.settings, f"finish:{i.id}")
    i.state = "completed"
    await db.execute(
        update(VoiceSession)
        .where(VoiceSession.consultant_interview_id == i.id)
        .values(status="completed", ended_at=datetime.now(UTC))
    )
    return svc.job_response(job)


@router.post("/interviews/{interview_id}/generate")
async def regenerate(
    interview_id: UUID, payload: GenerateInput, user: CurrentUser, db: DB, request: Request
) -> dict[str, Any]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    if i.state != "completed":
        raise svc.fail("interview_not_finished", "Finish the interview before regenerating.")
    return svc.job_response(
        await svc.enqueue(
            db, user, i, request.app.state.settings, payload.request_key, payload.instruction
        )
    )


@router.post("/articles/{article_id}/ai-requests")
async def request_article_change(
    article_id: UUID, payload: ArticleAIRequest, user: CurrentUser, db: DB, request: Request
) -> dict[str, Any]:
    article = await svc.owned_article(db, user.id, article_id, lock=True)
    draft = await db.scalar(select(ArticleDraft).where(ArticleDraft.article_id == article.id))
    if draft is None:
        raise svc.fail("draft_missing", "Create a draft before asking AI to change it.", 422)
    if not _matches_version(request.headers.get("if-unmodified-since"), draft.updated_at):
        raise svc.fail(
            "draft_conflict", "This draft changed elsewhere. Reload before requesting a change."
        )
    interview = await db.scalar(
        select(ConsultantInterview)
        .where(
            ConsultantInterview.article_id == article.id,
            ConsultantInterview.state == "completed",
        )
        .order_by(ConsultantInterview.created_at.desc())
    )
    if interview is None:
        raise svc.fail(
            "interview_not_finished", "Finish an interview before asking AI to edit.", 422
        )
    return svc.job_response(await svc.enqueue(
        db, user, interview, request.app.state.settings,
        payload.request_key, payload.instruction, request_kind="ask_ai",
    ))


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    job = await db.scalar(
        select(ConsultantGeneration).where(ConsultantGeneration.id == job_id).with_for_update()
    )
    if not job:
        raise svc.fail("job_not_found", "Generation not found.", 404)
    await svc.owned_article(db, user.id, job.article_id, lock=True)
    if job.state == "failed":
        running = await db.scalar(
            select(ConsultantGeneration.id).where(
                ConsultantGeneration.article_id == job.article_id,
                ConsultantGeneration.state.in_(["queued", "running"]),
            )
        )
        if running:
            raise svc.fail("generation_running", "Another draft is being generated.")
        job.state, job.error, job.attempts = "queued", None, 0
        # Regenerate from the immutable transcript snapshot instead of checking
        # the same failed candidate again.
        job.candidate = None
        job.stage = "plan" if job.snapshot.get("request_kind") == "ask_ai" else "write"
    return svc.job_response(job)


@router.post("/interviews/{interview_id}/voice/session")
async def voice_session(
    interview_id: UUID, payload: VoiceSessionCreate, user: CurrentUser, db: DB, request: Request
) -> dict[str, Any]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    if i.state == "completed":
        raise svc.fail("interview_finalized", "Start a follow-up interview to record more.")
    if not payload.sdp:
        raise svc.fail("voice_sdp_required", "An SDP offer is required.", 422)
    a = await svc.owned_article(db, user.id, i.article_id)
    p = await svc.profile(db, user)
    t = await svc.transcript(db, i)
    i.state = "active"
    await db.execute(
        update(VoiceSession)
        .where(
            VoiceSession.consultant_interview_id == i.id,
            VoiceSession.status == "connected",
        )
        .values(status="paused", ended_at=datetime.now(UTC))
    )
    context = ConsultantVoiceContext(
        p.display_name, a.working_title, a.notes, a.target_audience, i.questions
    )
    result = await OpenAIRealtimeService(request.app.state.settings).create_call(
        sdp=payload.sdp,
        interview=context,
        previous_turns=[
            TranscriptTurn.model_validate(turn)
            for turn in svc.effective_turns(t.turns, i.corrections)
        ],
    )
    record = VoiceSession(
        consultant_interview_id=i.id,
        provider="openai_live",
        status="connected",
        started_at=datetime.now(UTC),
        external_session_id=result.external_session_id,
    )
    db.add(record)
    await db.flush()
    return {
        "provider": "openai_live",
        "transport": "openai_webrtc",
        "session_id": record.id,
        "sdp": result.sdp,
    }


@router.post(
    "/interviews/{interview_id}/voice/sessions/{session_id}/completion-approval"
)
async def voice_completion_approval(
    interview_id: UUID,
    session_id: UUID,
    payload: VoiceCompletionApproval,
    user: CurrentUser,
    db: DB,
) -> dict[str, Any]:
    interview = await svc.owned_interview(db, user.id, interview_id, lock=True)
    session = await db.scalar(
        select(VoiceSession).where(
            VoiceSession.id == session_id,
            VoiceSession.consultant_interview_id == interview.id,
        )
    )
    if session is None:
        raise svc.fail("voice_session_not_found", "Voice session not found.", 404)
    return {
        "approved": True,
        "reason": payload.reason,
    }


@router.get(
    "/interviews/{interview_id}/voice/sessions/{session_id}/completion-status"
)
async def voice_completion_status(
    interview_id: UUID,
    session_id: UUID,
    user: CurrentUser,
    db: DB,
) -> dict[str, Any]:
    interview = await svc.owned_interview(db, user.id, interview_id)
    session = await db.scalar(
        select(VoiceSession).where(
            VoiceSession.id == session_id,
            VoiceSession.consultant_interview_id == interview.id,
        )
    )
    if session is None:
        raise svc.fail("voice_session_not_found", "Voice session not found.", 404)
    return {
        "approved": False,
        "reason": None,
    }


@router.post("/interviews/{interview_id}/answer")
async def answer(
    interview_id: UUID, payload: TypedAnswer, user: CurrentUser, db: DB, request: Request
) -> dict[str, Any]:
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    t = await svc.transcript(db, i)
    if i.state == "completed":
        raise svc.fail("interview_finalized", "This interview is complete.")
    i.state = "active"
    response_id = f"reply:{payload.item_id}"
    existing_reply = next((turn for turn in t.turns if turn["item_id"] == response_id), None)
    if existing_reply:
        return {"saved": True, "complete": existing_reply.get("complete", False)}
    final_question_active = bool(
        t.turns
        and t.turns[-1].get("speaker") == "interviewer"
        and svc.is_final_contribution_question(t.turns[-1].get("text", ""))
    )
    settings = request.app.state.settings
    if settings.openai_api_key is None:
        raise svc.fail("ai_unavailable", "The interviewer is not configured.", 503)
    if not t.turns:
        t.turns = [
            {
                "item_id": "opening",
                "speaker": "interviewer",
                "text": svc.TYPED_QUESTIONS[0],
            }
        ]
    if not any(turn["item_id"] == payload.item_id for turn in t.turns):
        t.turns = [
            *t.turns,
            {
                "item_id": payload.item_id,
                "speaker": "participant",
                "text": "[Question skipped]" if payload.skip else payload.text,
            },
        ]
    # Persist the answer before the provider call; a failed follow-up must not lose it.
    await db.commit()
    if final_question_active or svc.is_explicit_finish(payload.text):
        closing, complete = svc.typed_reply(
            final_question_active=final_question_active,
            answer=payload.text,
        )
        t = await svc.transcript(db, i)
        t.turns = [
            *t.turns,
            {
                "item_id": response_id,
                "speaker": "interviewer",
                "text": closing,
                "complete": complete,
            },
        ]
        return {"saved": True, "complete": complete}
    a = await svc.owned_article(db, user.id, i.article_id)
    try:
        async with AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=45
        ) as client:
            response = await client.responses.parse(
                model=settings.openai_interview_question_model,
                instructions=INTERVIEW_POLICY
                + (
                    "\nThis is a typed conversation. Return the next single question. "
                    "After sufficient coverage, ask the final contribution question in the policy "
                    "and set complete=false. Never set complete=true for normal coverage; the "
                    "application completes after the final answer. "
                    "Skipped questions are not source facts."
                ),
                input=source_json({"article": a.notes, "title": a.working_title, "turns": t.turns}),
                text_format=NextQuestion,
                reasoning={"effort": settings.openai_reasoning_effort},
                store=False,
            )
    except Exception as exc:
        raise svc.fail(
            "interviewer_unavailable",
            "Your answer is saved. Retry to continue, or finish the interview.",
            502,
        ) from exc
    result = response.output_parsed
    if not result:
        raise svc.fail(
            "interviewer_unavailable",
            "Your answer is saved. Please retry or finish the interview.",
            502,
        )
    i = await svc.owned_interview(db, user.id, interview_id, lock=True)
    t = await svc.transcript(db, i)
    next_question, complete = svc.typed_reply(
        final_question_active=False,
        answer=payload.text,
        proposed_question=result.question,
        proposed_complete=result.complete,
    )
    if i.state != "completed" and not any(turn["item_id"] == response_id for turn in t.turns):
        t.turns = [
            *t.turns,
            {
                "item_id": response_id,
                "speaker": "interviewer",
                "text": next_question,
                "complete": complete,
            },
        ]
    return {"saved": True, "complete": complete}


@router.put("/articles/{article_id}/draft")
async def save_draft(
    article_id: UUID, payload: ArticleDraftUpdate, user: CurrentUser, db: DB, request: Request
) -> dict[str, bool]:
    a = await svc.owned_article(db, user.id, article_id, lock=True)
    draft = await db.scalar(select(ArticleDraft).where(ArticleDraft.article_id == a.id))
    expected = request.headers.get("if-unmodified-since")
    if draft and not _matches_version(expected, draft.updated_at):
        raise svc.fail("draft_conflict", "This draft changed elsewhere. Reload before saving.")
    await svc.save_draft(db, a, [s.model_dump(mode="json") for s in payload.sections])
    return {"saved": True}


@router.post("/articles/{article_id}/findings/{finding_id}/repair-preview")
async def repair_finding_preview(
    article_id: UUID,
    finding_id: UUID,
    payload: FindingRepairInput,
    user: CurrentUser,
    db: DB,
    request: Request,
) -> dict[str, Any]:
    _, draft, finding, section, paragraph = await _owned_active_finding(
        db, user, article_id, finding_id, request
    )
    assert section is not None and paragraph is not None
    before = paragraph_text(paragraph)
    after = payload.replacement_paragraph
    if not after and finding.suggested_replacement:
        after = (
            before.replace(finding.claim_text, finding.suggested_replacement, 1)
            if finding.claim_text and finding.claim_text in before
            else finding.suggested_replacement
        )
    if not after:
        job = (
            await db.get(ConsultantGeneration, finding.generation_id)
            if finding.generation_id
            else None
        )
        settings = get_settings()
        writer = ConsultantWriter(settings, settings.consultant_model)
        repaired = await writer.repair_finding({
            "heading": section.get("title", ""),
            "paragraph": before,
            "finding": finding_response(finding),
            **_finding_evidence(finding, job),
        })
        after = repaired.replacement_paragraph
    focused = await _focused_review(finding, before, after, section, db)
    classification = (
        "unsupported"
        if any(item.classification == "unsupported" for item in focused)
        else "interpretation" if focused else "supported"
    )
    return {
        "finding_id": finding.id,
        "before": before,
        "after": after,
        "classification": classification,
        "findings": [item.model_dump(mode="json") for item in focused],
        "target_version": draft.updated_at.isoformat(),
    }


@router.post("/articles/{article_id}/findings/{finding_id}/apply")
async def apply_finding_repair(
    article_id: UUID,
    finding_id: UUID,
    payload: FindingRepairInput,
    user: CurrentUser,
    db: DB,
    request: Request,
) -> dict[str, Any]:
    if not payload.replacement_paragraph:
        raise svc.fail("replacement_required", "Approve a replacement before applying it.", 422)
    article, draft, finding, section, paragraph = await _owned_active_finding(
        db, user, article_id, finding_id, request
    )
    assert section is not None and paragraph is not None
    before = paragraph_text(paragraph)
    focused = await _focused_review(
        finding, before, payload.replacement_paragraph, section, db
    )
    sections = copy.deepcopy(draft.sections)
    _, target, _, _ = locate_paragraph(
        sections,
        finding.section_id,
        finding.paragraph_id,
        finding.section_index,
        finding.paragraph_index,
    )
    replace_paragraph_text(target, payload.replacement_paragraph)
    draft.sections = sections
    finding.status, finding.resolution_reason = "resolved", "repaired"
    anchored = anchor_findings(
        [
            item.model_copy(update={
                "section_index": finding.section_index,
                "paragraph_index": finding.paragraph_index,
            })
            for item in focused
        ],
        sections,
    )
    for item in anchored:
        db.add(ConsultantDraftFinding(
            id=item.id,
            article_id=article.id,
            generation_id=finding.generation_id,
            draft_id=draft.id,
            classification=item.classification,
            category=item.category,
            section_id=item.section_id,
            section_index=item.section_index,
            paragraph_id=item.paragraph_id,
            paragraph_index=item.paragraph_index,
            claim_text=item.claim_text,
            explanation=item.explanation,
            source_turn_ids=item.source_turn_ids,
            external_source_ids=item.external_source_ids,
            suggested_replacement=item.suggested_replacement,
            status="open",
        ))
    await db.flush()
    await db.refresh(draft)
    return {
        "saved": True,
        "draft_updated_at": draft.updated_at,
        "findings": [item.model_dump(mode="json") for item in anchored],
    }


@router.post("/articles/{article_id}/findings/{finding_id}/recheck")
async def recheck_finding(
    article_id: UUID,
    finding_id: UUID,
    user: CurrentUser,
    db: DB,
    request: Request,
) -> dict[str, Any]:
    _, _, finding, section, paragraph = await _owned_active_finding(
        db, user, article_id, finding_id, request, allow_removed=True
    )
    if section is None or paragraph is None:
        return {"finding": finding_response(finding)}
    current = paragraph_text(paragraph)
    focused = await _focused_review(finding, current, current, section, db)
    if not focused:
        finding.status, finding.resolution_reason = "resolved", "rechecked_supported"
    else:
        result = focused[0]
        finding.classification = result.classification or "unsupported"
        finding.category = result.category
        finding.claim_text = result.claim_text
        finding.explanation = result.explanation
        finding.suggested_replacement = result.suggested_replacement
        finding.source_turn_ids = result.source_turn_ids
        finding.external_source_ids = result.external_source_ids
    return {"finding": finding_response(finding)}


@router.post("/articles/{article_id}/revisions/{revision_id}/apply")
async def apply_revision(
    article_id: UUID, revision_id: UUID, user: CurrentUser, db: DB, request: Request
) -> dict[str, bool]:
    a = await svc.owned_article(db, user.id, article_id, lock=True)
    r = await db.scalar(
        select(ConsultantDraftRevision).where(
            ConsultantDraftRevision.id == revision_id,
            ConsultantDraftRevision.article_id == a.id,
            ConsultantDraftRevision.kind == "proposal",
        )
    )
    if not r:
        raise svc.fail("revision_not_found", "Revision not found.", 404)
    if r.generation_id:
        source_job = await db.get(ConsultantGeneration, r.generation_id)
        current_interviews = (
            await db.scalars(
                select(ConsultantInterview).where(ConsultantInterview.article_id == a.id)
            )
        ).all()
        if source_job and any(
            source_job.snapshot["transcript_revisions"].get(str(i.id)) != i.revision
            for i in current_interviews
        ):
            raise svc.fail(
                "stale_proposal",
                "The interview changed after this proposal. Generate an updated draft first.",
            )
    draft = await db.scalar(select(ArticleDraft).where(ArticleDraft.article_id == a.id))
    if draft and not _matches_version(request.headers.get("if-unmodified-since"), draft.updated_at):
        raise svc.fail("draft_conflict", "This draft changed elsewhere. Reload before applying.")
    if r.generation_id and source_job:
        base_version = source_job.snapshot.get("base_draft_updated_at")
        if base_version and (draft is None or draft.updated_at.isoformat() != base_version):
            raise svc.fail(
                "stale_proposal",
                "This draft changed after the suggestion was requested. Generate a new preview.",
            )
    await svc.save_draft(db, a, r.sections)
    return {"saved": True}


@router.delete("/articles/{article_id}/revisions/{revision_id}")
async def reject_revision(
    article_id: UUID, revision_id: UUID, user: CurrentUser, db: DB
) -> dict[str, bool]:
    article = await svc.owned_article(db, user.id, article_id, lock=True)
    await db.execute(delete(ConsultantDraftRevision).where(
        ConsultantDraftRevision.id == revision_id,
        ConsultantDraftRevision.article_id == article.id,
        ConsultantDraftRevision.kind == "proposal",
    ))
    return {"rejected": True}


@router.put("/articles/{article_id}/details")
async def update_details(
    article_id: UUID, payload: ArticleDetailsInput, user: CurrentUser, db: DB
) -> dict[str, bool]:
    a = await svc.owned_article(db, user.id, article_id, lock=True)
    running = await db.scalar(
        select(ConsultantGeneration.id).where(
            ConsultantGeneration.article_id == a.id,
            ConsultantGeneration.state.in_(["queued", "running"]),
        )
    )
    if running:
        raise svc.fail(
            "generation_running", "Wait for the current draft before editing article details."
        )
    a.working_title, a.notes = payload.title, payload.description
    return {"saved": True}
