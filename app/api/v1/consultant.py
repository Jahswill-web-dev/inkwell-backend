from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from openai import AsyncOpenAI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.db.models.article import Article
from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import (
    ConsultantDraftRevision,
    ConsultantGeneration,
    ConsultantInterview,
)
from app.db.models.voice_session import VoiceSession
from app.db.models.workspace import Workspace, WorkspaceMember
from app.db.session import get_db_session
from app.prompts.consultant import INTERVIEW_POLICY, source_json
from app.schemas.consultant import (
    ArticleDetailsInput,
    ConsultantArticleInput,
    CorrectionsInput,
    ExperienceInput,
    GenerateInput,
    NextQuestion,
    ProfileInput,
    TurnsInput,
    TypedAnswer,
    VoiceProfile,
    VoiceUpdate,
)
from app.schemas.draft import ArticleDraftUpdate
from app.schemas.interview_transcript import TranscriptTurn
from app.schemas.realtime import VoiceSessionCreate
from app.services import consultant_service as svc
from app.services.interview_context import ConsultantVoiceContext
from app.services.openai_realtime import OpenAIRealtimeService

router = APIRouter(prefix="/consultant", tags=["consultant"])
DB = Annotated[AsyncSession, Depends(get_db_session)]


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
        "voice": p.voice,
        "voice_revision": p.voice_revision,
        "suggested_voice": p.suggested_voice,
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


@router.put("/voice")
async def put_voice(payload: VoiceUpdate, user: CurrentUser, db: DB) -> dict[str, bool]:
    p = await svc.profile(db, user)
    if payload.action == "save":
        if payload.voice is None:
            raise svc.fail("voice_required", "Provide voice preferences.", 422)
        p.voice = payload.voice.model_dump()
    elif payload.action == "approve":
        if p.suggested_voice is None:
            raise svc.fail("no_suggestion", "There is no voice suggestion to approve.")
        p.voice = VoiceProfile.model_validate(p.suggested_voice).model_dump()
    elif payload.action == "reset":
        p.voice = {}
    if payload.action != "dismiss":
        p.voice_revision += 1
    p.suggested_voice = None
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
            .where(ConsultantDraftRevision.article_id == a.id)
            .order_by(ConsultantDraftRevision.created_at.desc())
        )
    ).all()
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
        "revisions": [
            {
                "id": r.id,
                "kind": r.kind,
                "sections": r.sections,
                "gaps": r.gaps,
                "created_at": r.created_at,
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
                {"id": str(index), "text": question} for index, question in enumerate(svc.QUESTIONS)
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
    settings = request.app.state.settings
    if settings.openai_api_key is None:
        raise svc.fail("ai_unavailable", "The interviewer is not configured.", 503)
    if not t.turns:
        t.turns = [{"item_id": "opening", "speaker": "interviewer", "text": svc.QUESTIONS[0]}]
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
    a = await svc.owned_article(db, user.id, i.article_id)
    try:
        async with AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=45
        ) as client:
            response = await client.responses.parse(
                model=settings.openai_interview_question_model,
                instructions=INTERVIEW_POLICY
                + (
                    "\nThis is a typed conversation. Return the next single question and "
                    "complete=true only when coverage is sufficient or the participant explicitly "
                    "finishes. Skipped questions are not source facts."
                ),
                input=source_json({"article": a.notes, "title": a.working_title, "turns": t.turns}),
                text_format=NextQuestion,
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
    if i.state != "completed" and not any(turn["item_id"] == response_id for turn in t.turns):
        t.turns = [
            *t.turns,
            {
                "item_id": response_id,
                "speaker": "interviewer",
                "text": result.question,
                "complete": result.complete,
            },
        ]
    return {"saved": True, "complete": result.complete}


@router.put("/articles/{article_id}/draft")
async def save_draft(
    article_id: UUID, payload: ArticleDraftUpdate, user: CurrentUser, db: DB, request: Request
) -> dict[str, bool]:
    a = await svc.owned_article(db, user.id, article_id, lock=True)
    draft = await db.scalar(select(ArticleDraft).where(ArticleDraft.article_id == a.id))
    expected = request.headers.get("if-unmodified-since")
    if draft and expected != draft.updated_at.isoformat():
        raise svc.fail("draft_conflict", "This draft changed elsewhere. Reload before saving.")
    await svc.save_revision(db, a, [s.model_dump(mode="json") for s in payload.sections], "edited")
    return {"saved": True}


@router.post("/articles/{article_id}/revisions/{revision_id}/apply")
async def apply_revision(
    article_id: UUID, revision_id: UUID, user: CurrentUser, db: DB, request: Request
) -> dict[str, bool]:
    a = await svc.owned_article(db, user.id, article_id, lock=True)
    r = await db.scalar(
        select(ConsultantDraftRevision).where(
            ConsultantDraftRevision.id == revision_id, ConsultantDraftRevision.article_id == a.id
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
    if draft and request.headers.get("if-unmodified-since") != draft.updated_at.isoformat():
        raise svc.fail("draft_conflict", "This draft changed elsewhere. Reload before applying.")
    await svc.save_revision(db, a, r.sections, "accepted")
    return {"saved": True}


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
