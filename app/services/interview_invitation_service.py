from __future__ import annotations

import secrets
from datetime import UTC, datetime, time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.interview_invitation import InterviewInvitation
from app.db.repositories.article import ArticleRepository
from app.db.repositories.interview_invitation import InterviewInvitationRepository
from app.db.repositories.workspace import WorkspaceRepository
from app.schemas.interview_invitation import (
    GuestInterviewResponse,
    GuestSessionData,
    GuestSessionUpdate,
    InterviewInvitationCreate,
    InterviewInvitationResponse,
)
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
)
from app.services.section_interview_ai import ClientInterviewQuestionGenerator

FALLBACK_QUESTIONS = [
    {
        "id": "key-message",
        "text": "What is the most important idea you want readers to take away from this article?",
        "kind": "core",
    },
    {
        "id": "experience",
        "text": "What first-hand experience gives you a distinctive perspective on this topic?",
        "kind": "core",
    },
    {
        "id": "example",
        "text": "Can you share a concrete example, story, or result that brings this idea to life?",
        "kind": "core",
    },
    {
        "id": "misconception",
        "text": "What do people commonly misunderstand about this topic?",
        "kind": "core",
    },
    {
        "id": "action",
        "text": "What should a reader do differently after reading the article?",
        "kind": "core",
    },
    {
        "id": "evidence",
        "text": (
            "Are there any facts, results, or claims the writer should verify before publishing?"
        ),
        "kind": "core",
    },
]


class InterviewInvitationService:
    def __init__(
        self,
        session: AsyncSession,
        question_generator: ClientInterviewQuestionGenerator | None = None,
    ) -> None:
        self.session = session
        self.invitations = InterviewInvitationRepository(session)
        self.articles = ArticleRepository(session)
        self.workspaces = WorkspaceRepository(session)
        self.question_generator = question_generator

    async def create(
        self, *, article_id: UUID, user_id: UUID, payload: InterviewInvitationCreate
    ) -> InterviewInvitationResponse:
        workspace_id = await self._workspace_id(user_id)
        article = await self.articles.get_in_workspace(article_id, workspace_id)
        if article is None:
            raise AppError(
                status_code=404, code="article_not_found", message="The article was not found"
            )

        # Revoke any previous active invitations for this article
        existing = await self.invitations.list_by_article(article_id, workspace_id)
        for inv in existing:
            if inv.status == "active":
                inv.status = "revoked"

        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)

        expires_at: datetime | None = None
        if payload.expires_on:
            try:
                date_val = datetime.strptime(payload.expires_on, "%Y-%m-%d").date()
                expires_at = datetime.combine(date_val, time.max, tzinfo=UTC)
            except ValueError:
                expires_at = None

        questions = await self._generate_questions(article)
        session_data = {
            "token": token,
            "state": "welcome",
            "questions": questions,
            "current_question_index": 0,
            "answers": [],
            "completion_reason": None,
            "final_detail_added": False,
            "draft_answer": "",
            "updated_at": now.isoformat(),
        }

        invitation = InterviewInvitation(
            article_id=article_id,
            workspace_id=workspace_id,
            participant_name=payload.participant_name,
            participant_email=str(payload.participant_email),
            token=token,
            status="active",
            expires_at=expires_at,
            progress_state="not_opened",
            questions_answered=0,
            estimated_questions=len(questions),
            opened_at=None,
            completed_at=None,
            session_data=session_data,
        )

        article.interview_method = "client"
        article.interviewee_name = payload.participant_name
        if article.status == "setup":
            article.status = "waiting_for_client"

        await self.invitations.add(invitation)
        await self.session.flush()

        persisted = await self.invitations.get_by_id_in_workspace(invitation.id, workspace_id)
        assert persisted is not None
        return self._to_response(persisted)

    async def get_latest_for_article(
        self, *, article_id: UUID, user_id: UUID
    ) -> InterviewInvitationResponse | None:
        workspace_id = await self._workspace_id(user_id)
        invitation = await self.invitations.get_latest_by_article(article_id, workspace_id)
        if invitation is None:
            return None
        return self._to_response(invitation)

    async def revoke(
        self, *, article_id: UUID, invitation_id: UUID, user_id: UUID
    ) -> InterviewInvitationResponse:
        workspace_id = await self._workspace_id(user_id)
        invitation = await self.invitations.get_by_id_in_workspace(invitation_id, workspace_id)
        if invitation is None or invitation.article_id != article_id:
            raise AppError(
                status_code=404, code="invitation_not_found", message="The invitation was not found"
            )

        invitation.status = "revoked"

        article = await self.articles.get_in_workspace(article_id, workspace_id)
        if article and article.status == "waiting_for_client":
            article.status = "setup"

        await self.session.flush()
        return self._to_response(invitation)

    async def get_guest_interview(self, *, token: str) -> GuestInterviewResponse:
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise AppError(
                status_code=404,
                code="invitation_not_found",
                message="This interview link isn't valid",
            )

        if invitation.status == "revoked":
            raise AppError(
                status_code=403,
                code="invitation_revoked",
                message="This interview link was revoked by the writer",
            )

        now = datetime.now(UTC)
        if invitation.expires_at and invitation.expires_at < now:
            raise AppError(
                status_code=410,
                code="invitation_expired",
                message="This interview link has expired",
            )

        if invitation.opened_at is None:
            invitation.opened_at = now
            if invitation.progress_state == "not_opened":
                invitation.progress_state = "opened"
            await self.session.flush()
            await self.session.refresh(invitation, attribute_names=["updated_at"])

        return GuestInterviewResponse(
            invitation=self._to_response(invitation),
            session=GuestSessionData.model_validate(invitation.session_data),
        )

    async def update_guest_session(
        self, *, token: str, payload: GuestSessionUpdate
    ) -> GuestInterviewResponse:
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise AppError(
                status_code=404,
                code="invitation_not_found",
                message="This interview link isn't valid",
            )

        if invitation.status == "revoked":
            raise AppError(
                status_code=403,
                code="invitation_revoked",
                message="This interview link was revoked by the writer",
            )

        now = datetime.now(UTC)
        if invitation.expires_at and invitation.expires_at < now:
            raise AppError(
                status_code=410,
                code="invitation_expired",
                message="This interview link has expired",
            )

        session_dict = dict(invitation.session_data)
        update_data = payload.model_dump(exclude_unset=True)
        session_dict.update(update_data)
        session_dict["updated_at"] = now.isoformat()

        answers = session_dict.get("answers", [])
        invitation.questions_answered = len(answers)
        state = session_dict.get("state", "active")

        if state == "completed":
            invitation.progress_state = "completed"
            if invitation.completed_at is None:
                invitation.completed_at = now
            if invitation.article:
                invitation.article.status = "ready_to_draft"
        elif len(answers) > 0:
            invitation.progress_state = "in_progress"
            if invitation.article and invitation.article.status in ("setup", "waiting_for_client"):
                invitation.article.status = "interview_in_progress"
        else:
            invitation.progress_state = "opened"

        invitation.session_data = session_dict
        await self.session.flush()
        await self.session.refresh(invitation, attribute_names=["updated_at"])

        return GuestInterviewResponse(
            invitation=self._to_response(invitation),
            session=GuestSessionData.model_validate(invitation.session_data),
        )

    async def _workspace_id(self, user_id: UUID) -> UUID:
        result = await self.workspaces.get_default_for_user(user_id)
        if result is None:
            raise AppError(
                status_code=404,
                code="workspace_not_found",
                message="The default workspace was not found",
            )
        return result[0].id

    async def _generate_questions(self, article: Article) -> list[dict[str, str]]:
        if self.question_generator is None:
            return FALLBACK_QUESTIONS

        context = {
            "working_title": article.working_title,
            "article_goal": article.article_goal,
            "content_type": article.content_type,
            "target_length": article.target_length,
            "target_audience": article.target_audience,
            "main_angle": article.main_angle,
            "key_message": article.key_message,
            "call_to_action": article.call_to_action,
            "tone": article.tone,
            "seo_keyword": article.seo_keyword,
        }
        try:
            result = await self.question_generator.generate_client_questions(context)
        except BriefProviderTimeoutError as exc:
            raise AppError(
                status_code=504,
                code="interview_question_generation_timeout",
                message="Interview question generation timed out",
            ) from exc
        except BriefProviderBlockedError as exc:
            raise AppError(
                status_code=422,
                code="interview_question_generation_blocked",
                message="The article context could not be processed",
            ) from exc
        except BriefProviderResponseError as exc:
            raise AppError(
                status_code=502,
                code="interview_question_generation_failed",
                message=str(exc) or "The generated interview questions were invalid",
            ) from exc
        except BriefProviderUnavailableError as exc:
            raise AppError(
                status_code=503,
                code="interview_question_generation_unavailable",
                message="Interview question generation is temporarily unavailable",
            ) from exc

        return [
            {"id": f"generated-{index}", "text": item.question, "kind": "core"}
            for index, item in enumerate(result.questions.questions, start=1)
        ]

    def _to_response(self, invitation: InterviewInvitation) -> InterviewInvitationResponse:
        article = invitation.article
        article_title = article.working_title if article else None
        client_name = article.client.name if article and article.client else None
        writer_name = article.assignee.username if article and article.assignee else None

        return InterviewInvitationResponse(
            id=invitation.id,
            article_id=invitation.article_id,
            article_title=article_title,
            client_name=client_name,
            writer_name=writer_name,
            participant_name=invitation.participant_name,
            participant_email=invitation.participant_email,
            token=invitation.token,
            status=invitation.status,
            expires_at=invitation.expires_at,
            progress_state=invitation.progress_state,
            questions_answered=invitation.questions_answered,
            estimated_questions=invitation.estimated_questions,
            opened_at=invitation.opened_at,
            completed_at=invitation.completed_at,
            created_at=invitation.created_at,
            updated_at=invitation.updated_at,
        )
