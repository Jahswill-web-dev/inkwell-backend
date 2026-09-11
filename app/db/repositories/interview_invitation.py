from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.article import Article
from app.db.models.interview_invitation import InterviewInvitation
from app.db.queries import Repository


class InterviewInvitationRepository(Repository[InterviewInvitation]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(InterviewInvitation, session)

    async def get_by_token(self, token: str) -> InterviewInvitation | None:
        result = await self.session.scalars(
            select(InterviewInvitation)
            .options(
                selectinload(InterviewInvitation.article).selectinload(Article.client),
                selectinload(InterviewInvitation.article).selectinload(Article.assignee),
            )
            .where(InterviewInvitation.token == token)
        )
        return result.first()

    async def get_by_id_in_workspace(
        self, invitation_id: UUID, workspace_id: UUID
    ) -> InterviewInvitation | None:
        result = await self.session.scalars(
            select(InterviewInvitation)
            .options(
                selectinload(InterviewInvitation.article).selectinload(Article.client),
                selectinload(InterviewInvitation.article).selectinload(Article.assignee),
            )
            .where(
                InterviewInvitation.id == invitation_id,
                InterviewInvitation.workspace_id == workspace_id,
            )
        )
        return result.first()

    async def get_latest_by_article(
        self, article_id: UUID, workspace_id: UUID
    ) -> InterviewInvitation | None:
        result = await self.session.scalars(
            select(InterviewInvitation)
            .options(
                selectinload(InterviewInvitation.article).selectinload(Article.client),
                selectinload(InterviewInvitation.article).selectinload(Article.assignee),
            )
            .where(
                InterviewInvitation.article_id == article_id,
                InterviewInvitation.workspace_id == workspace_id,
            )
            .order_by(desc(InterviewInvitation.created_at))
            .limit(1)
        )
        return result.first()

    async def list_by_article(
        self, article_id: UUID, workspace_id: UUID
    ) -> Sequence[InterviewInvitation]:
        result = await self.session.scalars(
            select(InterviewInvitation)
            .options(
                selectinload(InterviewInvitation.article).selectinload(Article.client),
                selectinload(InterviewInvitation.article).selectinload(Article.assignee),
            )
            .where(
                InterviewInvitation.article_id == article_id,
                InterviewInvitation.workspace_id == workspace_id,
            )
            .order_by(desc(InterviewInvitation.created_at))
        )
        return result.all()
