from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.db.models.article import Article
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_draft import ArticleDraft

SELECTED_TEXT_LIMIT = 12_000
OTHER_TEXT_LIMIT = 4_000
TOTAL_DRAFT_TEXT_LIMIT = 40_000


def build_draft_section_context(
    *,
    article: Article,
    brief: ArticleBrief,
    draft: ArticleDraft,
    outline_sections: list[dict[str, Any]],
    selected_section_id: UUID,
) -> dict[str, Any]:
    editor_text = bounded_draft_text(draft.sections, selected_section_id)
    outline_by_id = {
        str(section.get("id")): section for section in outline_sections if isinstance(section, dict)
    }
    draft_sections: list[dict[str, Any]] = []
    for section in draft.sections:
        outline_section = outline_by_id.get(str(section.get("outline_section_id")), {})
        draft_sections.append(
            {
                "id": section.get("id"),
                "title": section.get("title"),
                "goal": section.get("goal"),
                "outline_key_points": outline_section.get("key_points", []),
                "checklist": [
                    item.get("label")
                    for item in section.get("checklist", [])
                    if isinstance(item, dict) and isinstance(item.get("label"), str)
                ],
                "editor_text": editor_text.get(str(section.get("id")), ""),
            }
        )
    selected_id = str(selected_section_id)
    selected = next(section for section in draft_sections if section["id"] == selected_id)
    return {
        "selected_section": selected,
        "article": {
            "working_title": article.working_title,
            "article_goal": article.article_goal,
            "target_audience": article.target_audience,
            "notes": article.notes,
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
        "other_sections": [section for section in draft_sections if section["id"] != selected_id],
    }


def bounded_draft_text(sections: list[dict[str, Any]], selected_section_id: UUID) -> dict[str, str]:
    selected_id = str(selected_section_id)
    extracted = {
        str(section.get("id")): extract_lexical_text(section.get("editor_state"))
        for section in sections
    }
    result = {selected_id: extracted.get(selected_id, "")[:SELECTED_TEXT_LIMIT]}
    remaining = TOTAL_DRAFT_TEXT_LIMIT - len(result[selected_id])
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
    return "\n".join(block for child in children if (block := _node_text(child))).strip()


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
