"""Targeted Ask AI proposals; the saved Lexical draft remains the source of truth."""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from app.schemas.consultant import (
    ArticleEditAction,
    ArticleEditPlan,
    GeneratedDraft,
    GeneratedSection,
)
from app.schemas.draft import ArticleDraftUpdate
from app.services.consultant_writer import (
    ConsultantWriter,
    _plain_editor_text,
    editor_sections,
)


class AskAIClarification(Exception):
    def __init__(self, question: str) -> None:
        self.question = question


class AskAIAccuracyError(Exception):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues


def section_text(section: dict[str, Any]) -> str:
    return _plain_editor_text(json.loads(section["editor_state"])["root"])


def outline(sections: list[dict[str, Any]], request: str = "") -> list[dict[str, str]]:
    terms = set(re.findall(r"[a-z]{4,}", request.lower()))
    result = []
    for section in sections:
        body = section_text(section)
        matches = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", body)
            if terms & set(re.findall(r"[a-z]{4,}", sentence.lower()))
        ]
        result.append(
            {
                "id": section["id"],
                "heading": section["title"],
                "excerpt": body[:450],
                "matching_content": " ".join(matches[:2])[:500],
            }
        )
    return result


def validate_plan(plan: ArticleEditPlan, sections: list[dict[str, Any]]) -> None:
    if plan.clarification_question:
        raise AskAIClarification(plan.clarification_question)
    if not plan.actions:
        raise AskAIClarification("Which part of the article would you like to change?")
    ids = {section["id"] for section in sections}
    full = [action for action in plan.actions if action.kind == "full_rewrite"]
    if full and len(plan.actions) > 1:
        raise AskAIClarification(
            "Should I rewrite the whole article, or make the listed local changes?"
        )
    for action in plan.actions:
        if action.kind != "full_rewrite" and action.target_section_id not in ids:
            raise AskAIClarification("Which section should I change? Please name its heading.")
        if action.kind == "section_move":
            if (
                action.destination_section_id not in ids
                or action.destination_section_id == action.target_section_id
                or action.position is None
            ):
                raise AskAIClarification("Where should I move that section?")
    moves = [action.target_section_id for action in plan.actions if action.kind == "section_move"]
    if len(moves) != len(set(moves)):
        raise AskAIClarification("Where should each section end up? Please describe the order.")


def relevant_turns(
    sections: list[dict[str, Any]],
    actions: list[ArticleEditAction],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bound the source payload by direct overlap with the requested article change."""
    participant = [turn for turn in turns if turn["speaker"] == "participant"]
    text = " ".join(
        [action.instruction for action in actions]
        + [section["title"] + " " + section_text(section)[:1000] for section in sections]
    ).lower()
    words = set(re.findall(r"[a-z]{4,}", text))
    ranked = sorted(
        participant,
        key=lambda turn: len(words & set(re.findall(r"[a-z]{4,}", turn["text"].lower()))),
        reverse=True,
    )
    return ranked[:12]


def move_sections(
    sections: list[dict[str, Any]], actions: list[ArticleEditAction]
) -> tuple[list[dict[str, Any]], bool]:
    result = list(sections)
    moved = False
    for action in actions:
        if action.kind != "section_move":
            continue
        target = next(section for section in result if section["id"] == action.target_section_id)
        result.remove(target)
        destination = next(
            index
            for index, section in enumerate(result)
            if section["id"] == action.destination_section_id
        )
        result.insert(destination + (action.position == "after"), target)
        moved = True
    return result, moved


def check_editor_sections(sections: list[dict[str, Any]]) -> None:
    ArticleDraftUpdate.model_validate({"sections": sections})
    for section in sections:

        def visit(node: Any) -> None:
            if not isinstance(node, dict):
                raise ValueError("Invalid editor node")
            if node.get("type") == "link":
                url = urlparse(str(node.get("url", "")))
                if url.scheme not in {"http", "https"} or not url.netloc:
                    raise ValueError("Article contains an invalid source link")
            for child in node.get("children", []):
                visit(child)

        visit(json.loads(section["editor_state"])["root"])


async def prepare_ask_ai(
    writer: ConsultantWriter,
    snapshot: dict[str, Any],
    progress: Callable[[str], Awaitable[None]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return proposal sections, private candidate metadata, and updated snapshot."""
    original = snapshot["base_sections"]
    assert original
    started = time.perf_counter()
    await progress("plan")
    plan = await writer.plan_article_edit(
        {
            "request": snapshot["instruction"],
            "article_title": snapshot["article"]["title"],
            "outline": outline(original, snapshot["instruction"]),
        }
    )
    validate_plan(plan, original)
    writer.usage.setdefault("stage_seconds", {})["plan"] = round(time.perf_counter() - started, 3)

    current, moved = move_sections(copy.deepcopy(original), plan.actions)
    actions = [action for action in plan.actions if action.kind in {"text_edit", "addition"}]
    relevant_sections = [
        section for section in current if section["id"] in {a.target_section_id for a in actions}
    ]
    selected_turns = relevant_turns(relevant_sections, actions, snapshot["turns"])
    research_needed = bool(
        re.search(
            r"\b(source|sources|citations?|research|stud(?:y|ies)|statistics?|"
            r"real.world|case stud(?:y|ies)|public example)\b",
            snapshot["instruction"],
            re.IGNORECASE,
        )
    )
    if research_needed:
        await progress("research")
        research = await writer.research(
            {
                "article": snapshot["article"],
                "instruction": snapshot["instruction"],
                "research_sections": outline(relevant_sections or current),
            }
        )
        snapshot = {**snapshot, "external_research": research}

    changed: list[GeneratedSection] = []
    changed_ids: list[str] = []
    summary = ""
    if plan.actions[0].kind == "full_rewrite":
        await progress("write")
        start = time.perf_counter()
        compact = {
            "article": snapshot["article"],
            "instruction": snapshot["instruction"],
            "request_kind": "ask_ai",
            "base_sections": [
                {"title": section["title"], "text": section_text(section)} for section in current
            ],
            "turns": snapshot["turns"],
            "external_research": snapshot.get("external_research"),
        }
        draft = await writer.write(compact)
        changed = draft.sections
        current = editor_sections(draft, snapshot.get("external_research", {}).get("sources", []))
        changed_ids = [section["id"] for section in current]
        summary = draft.summary
        writer.usage.setdefault("stage_seconds", {})["write"] = round(
            time.perf_counter() - start, 3
        )
    elif actions:
        await progress("write")
        start = time.perf_counter()
        edit_targets = []
        for section in relevant_sections:
            index = next(i for i, item in enumerate(current) if item["id"] == section["id"])
            edit_targets.append(
                {
                    "section_index": index,
                    "section_id": section["id"],
                    "heading": section["title"],
                    "content": section_text(section),
                    "actions": [
                        a.model_dump() for a in actions if a.target_section_id == section["id"]
                    ],
                    "neighbors": [
                        outline(current)[i] for i in (index - 1, index + 1) if 0 <= i < len(current)
                    ],
                }
            )
        edits = await writer.edit_article_sections(
            {
                "request": snapshot["instruction"],
                "article": snapshot["article"],
                "outline": [{"id": s["id"], "heading": s["title"]} for s in current],
                "targets": edit_targets,
                "participant_turns": selected_turns,
                "external_research": snapshot.get("external_research"),
            }
        )
        indices = [repair.section_index for repair in edits.repairs]
        expected = {target["section_index"] for target in edit_targets}
        if len(indices) != len(expected) or set(indices) != expected:
            raise AskAIAccuracyError(["The edit did not return every requested section."])
        for repair in edits.repairs:
            changed_section = repair.section
            changed.append(changed_section)
            generated = editor_sections(
                GeneratedDraft(title="", sections=[changed_section], gaps=[]),
                snapshot.get("external_research", {}).get("sources", []),
            )[0]
            old = current[repair.section_index]
            current[repair.section_index] = {
                **old,
                "title": generated["title"],
                "editor_state": generated["editor_state"],
            }
            changed_ids.append(old["id"])
        summary = edits.summary
        writer.usage.setdefault("stage_seconds", {})["write"] = round(
            time.perf_counter() - start, 3
        )

    await progress("saving")
    check_editor_sections(current)

    if not summary:
        summary = "Moved the requested section." if moved else "Updated the requested section."
    elif moved:
        summary = f"{summary} Reordered the requested sections."
    if current == original:
        raise AskAIClarification(
            "I couldn't make a supported change. Could you add more detail to your request?"
        )
    candidate: dict[str, Any] = {
        "summary": summary,
        "plan": plan.model_dump(),
        "changed_section_ids": changed_ids,
    }
    candidate["findings"] = []
    return current, candidate, snapshot
