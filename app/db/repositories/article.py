from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.article import Article
from app.db.models.workspace import WorkspaceMember
from app.db.queries import Repository


class ArticleRepository(Repository[Article]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Article, session)

    async def get_owned(self, article_id: UUID, user_id: UUID) -> Article | None:
        result = await self.session.scalars(
            select(Article)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Article.workspace_id)
            .options(selectinload(Article.client), selectinload(Article.assignee))
            .where(Article.id == article_id, WorkspaceMember.user_id == user_id)
        )
        return result.first()

    async def get_in_workspace(self, article_id: UUID, workspace_id: UUID) -> Article | None:
        result = await self.session.scalars(
            select(Article)
            .options(selectinload(Article.client), selectinload(Article.assignee))
            .where(Article.id == article_id, Article.workspace_id == workspace_id)
        )
        return result.first()

    async def list_owned(self, user_id: UUID, *, offset: int, limit: int) -> Sequence[Article]:
        result = await self.session.scalars(
            select(Article)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Article.workspace_id)
            .options(selectinload(Article.client), selectinload(Article.assignee))
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Article.created_at.desc(), Article.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def count_owned(self, user_id: UUID) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Article)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Article.workspace_id)
            .where(WorkspaceMember.user_id == user_id)
        )
        return count or 0
