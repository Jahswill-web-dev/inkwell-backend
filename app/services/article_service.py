from collections.abc import Sequence
from uuid import UUID

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.repositories.article import ArticleRepository
from app.schemas.article import ArticleCreate, ArticleUpdate


class ArticleService:
    def __init__(self, articles: ArticleRepository) -> None:
        self.articles = articles

    async def create(self, *, user_id: UUID, payload: ArticleCreate) -> Article:
        article = Article(user_id=user_id, **payload.model_dump(mode="json"))
        return await self.articles.add(article)

    async def list(
        self, *, user_id: UUID, offset: int, limit: int
    ) -> tuple[Sequence[Article], int]:
        items = await self.articles.list_owned(user_id, offset=offset, limit=limit)
        total = await self.articles.count_owned(user_id)
        return items, total

    async def get(self, *, article_id: UUID, user_id: UUID) -> Article:
        article = await self.articles.get_owned(article_id, user_id)
        if article is None:
            raise _article_not_found()
        return article

    async def update(self, *, article_id: UUID, user_id: UUID, payload: ArticleUpdate) -> Article:
        article = await self.get(article_id=article_id, user_id=user_id)
        for field, value in payload.model_dump(exclude_unset=True, mode="json").items():
            setattr(article, field, value)
        await self.articles.session.flush()
        await self.articles.session.refresh(article)
        return article

    async def delete(self, *, article_id: UUID, user_id: UUID) -> None:
        article = await self.get(article_id=article_id, user_id=user_id)
        await self.articles.delete(article)


def _article_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="article_not_found",
        message="The article was not found",
    )
