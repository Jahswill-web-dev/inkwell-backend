from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.article_draft import ArticleDraft


class ArticleDraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_article(self, article_id: UUID) -> ArticleDraft | None:
        result = await self.session.scalars(
            select(ArticleDraft).where(ArticleDraft.article_id == article_id)
        )
        return result.first()

    async def create_if_missing(
        self, *, article_id: UUID, sections: list[dict[str, Any]]
    ) -> ArticleDraft:
        statement = (
            insert(ArticleDraft)
            .values(id=uuid4(), article_id=article_id, sections=sections)
            .on_conflict_do_nothing(constraint="uq_article_drafts_article_id")
            .returning(ArticleDraft)
        )
        draft = await self.session.scalar(statement)
        if draft is not None:
            return draft
        existing = await self.get_for_article(article_id)
        assert existing is not None
        return existing

    async def update_sections(
        self, draft: ArticleDraft, sections: list[dict[str, Any]]
    ) -> ArticleDraft:
        draft.sections = sections
        await self.session.flush()
        await self.session.refresh(draft)
        return draft
