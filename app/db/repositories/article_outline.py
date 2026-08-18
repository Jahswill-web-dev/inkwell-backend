from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.article_outline import ArticleOutline
from app.schemas.outline import GeneratedOutline


class ArticleOutlineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_article(self, article_id: UUID) -> ArticleOutline | None:
        result = await self.session.scalars(
            select(ArticleOutline).where(ArticleOutline.article_id == article_id)
        )
        return result.first()

    async def upsert(
        self,
        *,
        article_id: UUID,
        content: GeneratedOutline,
        source_brief_revision: int,
        model_id: str,
        prompt_version: str,
        input_token_count: int | None,
        output_token_count: int | None,
        generation_duration_ms: int,
    ) -> ArticleOutline:
        values: dict[str, Any] = {
            "sections": [section.model_dump(mode="json") for section in content.sections],
            "source_brief_revision": source_brief_revision,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "input_token_count": input_token_count,
            "output_token_count": output_token_count,
            "generation_duration_ms": generation_duration_ms,
        }
        statement = (
            insert(ArticleOutline)
            .values(id=uuid4(), article_id=article_id, **values)
            .on_conflict_do_update(
                constraint="uq_article_outlines_article_id",
                set_={**values, "updated_at": func.now()},
            )
            .returning(ArticleOutline)
        )
        outline = await self.session.scalar(statement)
        assert outline is not None
        return outline

    async def update_sections(
        self, outline: ArticleOutline, sections: list[dict[str, Any]]
    ) -> ArticleOutline:
        outline.sections = sections
        await self.session.flush()
        await self.session.refresh(outline)
        return outline

    async def delete(self, outline: ArticleOutline) -> None:
        await self.session.delete(outline)
        await self.session.flush()
