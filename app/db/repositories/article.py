from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.article import Article
from app.db.queries import Repository


class ArticleRepository(Repository[Article]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Article, session)

    async def get_owned(self, article_id: UUID, user_id: UUID) -> Article | None:
        result = await self.session.scalars(
            select(Article).where(Article.id == article_id, Article.user_id == user_id)
        )
        return result.first()

    async def list_owned(self, user_id: UUID, *, offset: int, limit: int) -> Sequence[Article]:
        result = await self.session.scalars(
            select(Article)
            .where(Article.user_id == user_id)
            .order_by(Article.created_at.desc(), Article.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def count_owned(self, user_id: UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).select_from(Article).where(Article.user_id == user_id)
        )
        return count or 0
