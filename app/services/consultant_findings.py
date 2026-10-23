from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consultant import ConsultantDraftFinding
from app.schemas.consultant import DraftFinding


def _root(section: dict[str, Any]) -> dict[str, Any]:
    state = section.get("editor_state", {})
    if isinstance(state, str):
        state = json.loads(state)
    root = state.setdefault("root", {})
    return root if isinstance(root, dict) else {}


def paragraph_text(node: dict[str, Any]) -> str:
    pieces: list[str] = []

    def visit(item: dict[str, Any]) -> None:
        if item.get("type") == "text":
            pieces.append(str(item.get("text", "")))
        for child in item.get("children", []):
            if isinstance(child, dict):
                visit(child)

    children = node.get("children", [])
    for child in children:
        if (
            isinstance(child, dict)
            and child.get("type") == "text"
            and str(child.get("text", "")).strip().startswith("Source:")
        ):
            break
        if isinstance(child, dict):
            visit(child)
    return "".join(pieces).strip()


def ensure_editor_anchors(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add durable IDs without changing visible content or legacy section shapes."""
    for section in sections:
        try:
            section["id"] = str(UUID(str(section.get("id"))))
        except (TypeError, ValueError):
            section["id"] = str(uuid4())
        root = _root(section)
        for node in root.get("children", []):
            if node.get("type") in {"paragraph", "anchored-paragraph"}:
                node.setdefault("paragraphId", str(uuid4()))
                node["type"] = "anchored-paragraph"
        section["editor_state"] = json.dumps({"root": root})
    return sections


def locate_paragraph(
    sections: list[dict[str, Any]], section_id: UUID | str, paragraph_id: UUID | str | None,
    section_index: int, paragraph_index: int | None,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    wanted_section = str(section_id)
    section_match = next(
        (i for i, section in enumerate(sections) if str(section.get("id")) == wanted_section),
        None,
    )
    resolved_section_index = section_match if section_match is not None else section_index
    if section_id and section_match is None:
        raise LookupError("section_not_found")
    if not 0 <= resolved_section_index < len(sections):
        raise LookupError("section_not_found")
    section = sections[resolved_section_index]
    paragraphs = [
        node
        for node in _root(section).get("children", [])
        if node.get("type") in {"paragraph", "anchored-paragraph"}
    ]
    wanted_paragraph = str(paragraph_id) if paragraph_id else None
    paragraph_match = next(
        (
            i
            for i, node in enumerate(paragraphs)
            if wanted_paragraph and str(node.get("paragraphId")) == wanted_paragraph
        ),
        None,
    )
    resolved_paragraph_index = (
        paragraph_match
        if paragraph_match is not None
        else paragraph_index if paragraph_index is not None else 0
    )
    if paragraph_id and paragraph_match is None:
        raise LookupError("paragraph_not_found")
    if not 0 <= resolved_paragraph_index < len(paragraphs):
        raise LookupError("paragraph_not_found")
    return (
        section,
        paragraphs[resolved_paragraph_index],
        resolved_section_index,
        resolved_paragraph_index,
    )


def replace_paragraph_text(node: dict[str, Any], text: str) -> None:
    children = node.get("children", [])
    source_at = next(
        (
            index
            for index, child in enumerate(children)
            if child.get("type") == "text"
            and str(child.get("text", "")).strip().startswith("Source:")
        ),
        len(children),
    )
    source_nodes = children[source_at:]
    node["children"] = [{
        "type": "text", "version": 1, "text": text, "format": 0,
        "detail": 0, "mode": "normal", "style": "",
    }, *source_nodes]


def anchor_findings(
    findings: list[DraftFinding], sections: list[dict[str, Any]]
) -> list[DraftFinding]:
    ensure_editor_anchors(sections)
    result: list[DraftFinding] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for finding in findings:
        if finding.classification == "supported" or finding.section_index >= len(sections):
            continue
        section = sections[finding.section_index]
        paragraphs = [
            node
            for node in _root(section).get("children", [])
            if node.get("type") in {"paragraph", "anchored-paragraph"}
        ]
        paragraph_index = finding.paragraph_index
        paragraph = (
            paragraphs[paragraph_index]
            if paragraph_index is not None and paragraph_index < len(paragraphs)
            else None
        )
        claim = re.sub(r"\s+", " ", finding.claim_text).strip()
        key = (
            str(section["id"]),
            str(paragraph.get("paragraphId", "")) if paragraph else "",
            finding.classification or "unsupported",
            finding.category,
            claim.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(finding.model_copy(update={
            "id": finding.id or uuid4(),
            "section_id": UUID(str(section["id"])),
            "paragraph_id": UUID(str(paragraph["paragraphId"])) if paragraph else None,
            "claim_text": claim,
        }))
    return result


async def persist_findings(
    session: AsyncSession,
    *,
    article_id: UUID,
    generation_id: UUID | None,
    findings: list[DraftFinding],
    sections: list[dict[str, Any]],
    draft_id: UUID | None = None,
    revision_id: UUID | None = None,
) -> list[ConsultantDraftFinding]:
    if (draft_id is None) == (revision_id is None):
        raise ValueError("A finding must belong to exactly one draft or revision")
    target = (
        ConsultantDraftFinding.draft_id == draft_id
        if draft_id is not None
        else ConsultantDraftFinding.revision_id == revision_id
    )
    await session.execute(delete(ConsultantDraftFinding).where(target))
    anchored = anchor_findings(findings, sections)
    rows: list[ConsultantDraftFinding] = []
    for item in anchored:
        row = ConsultantDraftFinding(
            id=item.id,
            article_id=article_id,
            generation_id=generation_id,
            draft_id=draft_id,
            revision_id=revision_id,
            classification=item.classification,
            category=item.category,
            section_id=item.section_id,
            section_index=item.section_index,
            paragraph_id=item.paragraph_id,
            paragraph_index=item.paragraph_index,
            claim_text=item.claim_text,
            explanation=item.explanation,
            source_turn_ids=item.source_turn_ids,
            external_source_ids=item.external_source_ids,
            suggested_replacement=item.suggested_replacement,
            status=item.status,
            resolution_reason=item.resolution_reason,
        )
        session.add(row)
        rows.append(row)
    return rows


def finding_response(item: ConsultantDraftFinding) -> dict[str, Any]:
    return {
        "id": item.id,
        "classification": item.classification,
        "category": item.category,
        "section_id": item.section_id,
        "section_index": item.section_index,
        "paragraph_id": item.paragraph_id,
        "paragraph_index": item.paragraph_index,
        "claim_text": item.claim_text,
        "explanation": item.explanation,
        "source_turn_ids": item.source_turn_ids,
        "external_source_ids": item.external_source_ids,
        "suggested_replacement": item.suggested_replacement,
        "status": item.status,
        "resolution_reason": item.resolution_reason,
    }
