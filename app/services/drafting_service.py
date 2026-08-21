from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article_draft import ArticleDraft
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_draft import ArticleDraftRepository
from app.db.repositories.article_outline import ArticleOutlineRepository
from app.schemas.draft import ArticleDraftResponse, ArticleDraftUpdate
from app.schemas.outline import ArticleOutlineSection

EMPTY_EDITOR_STATE = json.dumps(
    {
        "root": {
            "children": [],
            "direction": "ltr",
            "format": "",
            "indent": 0,
            "type": "root",
            "version": 1,
        }
    },
    separators=(",", ":"),
)


class ArticleDraftService:
    def __init__(self, session: AsyncSession) -> None:
        self.articles = ArticleRepository(session)
        self.outlines = ArticleOutlineRepository(session)
        self.drafts = ArticleDraftRepository(session)

    async def get(self, *, article_id: UUID, user_id: UUID) -> ArticleDraftResponse:
        await self._require_owned_article(article_id=article_id, user_id=user_id)
        draft = await self._get_draft(article_id)
        outline = await self.outlines.get_for_article(article_id)
        outline_sections = (
            []
            if outline is None
            else [ArticleOutlineSection.model_validate(section) for section in outline.sections]
        )
        reconciled = reconcile_sections(draft.sections, outline_sections)
        if reconciled != draft.sections:
            draft = await self.drafts.update_sections(draft, reconciled)
        return draft_response(draft)

    async def create(self, *, article_id: UUID, user_id: UUID) -> ArticleDraftResponse:
        await self._require_owned_article(article_id=article_id, user_id=user_id)
        existing = await self.drafts.get_for_article(article_id)
        if existing is not None:
            return draft_response(existing)
        outline = await self.outlines.get_for_article(article_id)
        if outline is None:
            raise AppError(status_code=404, code="outline_not_found", message="Outline not found.")
        sections = [
            new_draft_section(ArticleOutlineSection.model_validate(section))
            for section in outline.sections
        ]
        return draft_response(
            await self.drafts.create_if_missing(article_id=article_id, sections=sections)
        )

    async def update(
        self, *, article_id: UUID, user_id: UUID, payload: ArticleDraftUpdate
    ) -> ArticleDraftResponse:
        await self._require_owned_article(article_id=article_id, user_id=user_id)
        draft = await self._get_draft(article_id)
        sections = [section.model_dump(mode="json") for section in payload.sections]
        if sections != draft.sections:
            draft = await self.drafts.update_sections(draft, sections)
        return draft_response(draft)

    async def _require_owned_article(self, *, article_id: UUID, user_id: UUID) -> None:
        if await self.articles.get_owned(article_id, user_id) is None:
            raise AppError(status_code=404, code="article_not_found", message="Article not found.")

    async def _get_draft(self, article_id: UUID) -> ArticleDraft:
        draft = await self.drafts.get_for_article(article_id)
        if draft is None:
            raise AppError(status_code=404, code="draft_not_found", message="Draft not found.")
        return draft


def new_draft_section(section: ArticleOutlineSection) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "outline_section_id": str(section.id),
        "title": section.heading,
        "goal": section.purpose,
        "checklist": [],
        "editor_state": EMPTY_EDITOR_STATE,
    }


def reconcile_sections(
    stored_sections: list[dict[str, Any]], outline_sections: list[ArticleOutlineSection]
) -> list[dict[str, Any]]:
    current = {str(section.id): section for section in outline_sections}
    matched: set[str] = set()
    reconciled: list[dict[str, Any]] = []
    for stored in stored_sections:
        section = dict(stored)
        link = section.get("outline_section_id")
        outline_section = current.get(link) if isinstance(link, str) else None
        if outline_section is None:
            section["outline_section_id"] = None
        else:
            assert isinstance(link, str)
            matched.add(link)
            section["title"] = outline_section.heading
            section["goal"] = outline_section.purpose
        reconciled.append(section)
    reconciled.extend(
        new_draft_section(section) for section in outline_sections if str(section.id) not in matched
    )
    return reconciled


def draft_response(draft: ArticleDraft) -> ArticleDraftResponse:
    return ArticleDraftResponse.model_validate(draft, from_attributes=True)
