from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_draft import ArticleDraft
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_brief import ArticleBriefRepository
from app.db.repositories.article_draft import ArticleDraftRepository
from app.db.repositories.article_outline import ArticleOutlineRepository
from app.prompts.talking_points import PROMPT_VERSION
from app.schemas.outline import ArticleOutlineSection
from app.schemas.talking_points import TalkingPointsResponse
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    TalkingPointsGenerator,
    TalkingPointsSource,
)
from app.services.drafting_service import reconcile_sections

logger = logging.getLogger(__name__)

SELECTED_TEXT_LIMIT = 12_000
OTHER_TEXT_LIMIT = 4_000
TOTAL_DRAFT_TEXT_LIMIT = 40_000


class TalkingPointsService:
    def __init__(self, session: AsyncSession, generator: TalkingPointsGenerator) -> None:
        self.articles = ArticleRepository(session)
        self.briefs = ArticleBriefRepository(session)
        self.drafts = ArticleDraftRepository(session)
        self.outlines = ArticleOutlineRepository(session)
        self.generator = generator

    async def generate(
        self,
        *,
        article_id: UUID,
        section_id: UUID,
        user_id: UUID,
        instruction: str | None,
    ) -> TalkingPointsResponse:
        article = await self.articles.get_owned(article_id, user_id)
        if article is None:
            raise _error(404, "article_not_found", "Article not found")

        draft = await self.drafts.get_for_article(article_id)
        if draft is None:
            raise _error(404, "draft_not_found", "Draft not found")

        brief = await self.briefs.get_for_article(article_id)
        if brief is None:
            raise _error(404, "brief_not_found", "Article brief not found")

        outline = await self.outlines.get_for_article(article_id)
        outline_sections = (
            []
            if outline is None
            else [ArticleOutlineSection.model_validate(section) for section in outline.sections]
        )
        reconciled = reconcile_sections(draft.sections, outline_sections)
        if reconciled != draft.sections:
            draft = await self.drafts.update_sections(draft, reconciled)

        selected = next(
            (section for section in draft.sections if section.get("id") == str(section_id)),
            None,
        )
        if selected is None:
            raise _error(404, "draft_section_not_found", "Draft section not found")

        context = build_talking_points_context(
            article=article,
            brief=brief,
            draft=draft,
            outline_sections=outline.sections if outline is not None else [],
            selected_section_id=section_id,
        )
        started_at = perf_counter()
        try:
            result = await self.generator.generate(
                TalkingPointsSource(context=context, instruction=instruction)
            )
        except BriefProviderTimeoutError as exc:
            raise _error(
                504, "talking_points_generation_timeout", "Talking-point generation timed out"
            ) from exc
        except BriefProviderBlockedError as exc:
            raise _error(
                422,
                "talking_points_generation_blocked",
                "The article content could not be processed",
            ) from exc
        except BriefProviderResponseError as exc:
            raise _error(
                502,
                "talking_points_generation_failed",
                "The generated talking points were invalid",
            ) from exc
        except BriefProviderUnavailableError as exc:
            raise _error(
                503,
                "talking_points_generation_unavailable",
                "Talking-point generation is temporarily unavailable",
            ) from exc

        duration_ms = round((perf_counter() - started_at) * 1000)
        logger.info(
            "Talking points generated article_id=%s section_id=%s prompt_version=%s model_id=%s "
            "duration_ms=%s input_tokens=%s output_tokens=%s",
            article_id,
            section_id,
            PROMPT_VERSION,
            result.model_id,
            duration_ms,
            result.input_token_count,
            result.output_token_count,
        )
        return TalkingPointsResponse(
            section_id=section_id,
            points=result.talking_points.points,
        )


def build_talking_points_context(
    *,
    article: Article,
    brief: ArticleBrief,
    draft: ArticleDraft,
    outline_sections: list[dict[str, Any]],
    selected_section_id: UUID,
) -> dict[str, object]:
    editor_text = bounded_draft_text(draft.sections, selected_section_id)
    draft_sections = [
        {
            "id": section.get("id"),
            "outline_section_id": section.get("outline_section_id"),
            "title": section.get("title"),
            "goal": section.get("goal"),
            "checklist": [
                item.get("label")
                for item in section.get("checklist", [])
                if isinstance(item, dict) and isinstance(item.get("label"), str)
            ],
            "editor_text": editor_text.get(str(section.get("id")), ""),
        }
        for section in draft.sections
    ]
    return {
        "article": {
            "working_title": article.working_title,
            "notes": article.notes,
            "target_audience": article.target_audience,
            "article_goal": article.article_goal,
        },
        "brief": {
            "summary": brief.summary,
            "core_angle": brief.core_angle,
            "audience_insights": brief.audience_insights,
            "tone_and_style": brief.tone_and_style,
            "key_takeaways": brief.key_takeaways,
            "evidence_gaps": brief.evidence_gaps,
            "call_to_action": brief.call_to_action,
        },
        "outline": outline_sections,
        "draft_sections": draft_sections,
        "selected_section_id": str(selected_section_id),
    }


def bounded_draft_text(
    sections: list[dict[str, Any]], selected_section_id: UUID
) -> dict[str, str]:
    selected_id = str(selected_section_id)
    extracted = {
        str(section.get("id")): extract_lexical_text(section.get("editor_state"))
        for section in sections
    }
    result: dict[str, str] = {}
    selected_text = extracted.get(selected_id, "")[:SELECTED_TEXT_LIMIT]
    result[selected_id] = selected_text
    remaining = TOTAL_DRAFT_TEXT_LIMIT - len(selected_text)
    for section in sections:
        section_id = str(section.get("id"))
        if section_id == selected_id:
            continue
        text = extracted[section_id][: min(OTHER_TEXT_LIMIT, remaining)]
        result[section_id] = text
        remaining -= len(text)
    return result


def extract_lexical_text(editor_state: object) -> str:
    if not isinstance(editor_state, str):
        return ""
    try:
        document = json.loads(editor_state)
    except (TypeError, ValueError):
        return ""
    if not isinstance(document, dict) or not isinstance(document.get("root"), dict):
        return ""
    children = document["root"].get("children")
    if not isinstance(children, list):
        return ""
    blocks = [_node_text(child) for child in children]
    return "\n".join(block for block in blocks if block).strip()


def _node_text(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "linebreak":
        return "\n"
    own_text = node.get("text")
    text = own_text if isinstance(own_text, str) else ""
    children = node.get("children")
    if isinstance(children, list):
        text += "".join(_node_text(child) for child in children)
    return text


def _error(status_code: int, code: str, message: str) -> AppError:
    return AppError(status_code=status_code, code=code, message=message)
