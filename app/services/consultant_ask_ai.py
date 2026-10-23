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
    DraftFinding,
    GeneratedDraft,
    GeneratedSection,
)
from app.schemas.draft import ArticleDraftUpdate
from app.services.consultant_findings import anchor_findings
from app.services.consultant_writer import (
    ConsultantWriter,
    _plain_editor_text,
    editor_sections,
    sanitize_draft_quotes,
    validate_draft_findings,
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
    review_findings: list[DraftFinding] = []
    flow_review_findings: list[DraftFinding] = []
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
        draft = sanitize_draft_quotes(draft, snapshot["turns"])
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
            changed_section = sanitize_draft_quotes(
                GeneratedDraft(
                    title=snapshot["article"]["title"], sections=[repair.section], gaps=[]
                ),
                snapshot["turns"],
            ).sections[0]
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

    await progress("check")
    check_editor_sections(current)
    if changed:
        draft = GeneratedDraft(title=snapshot["article"]["title"], sections=changed, gaps=[])
        findings = validate_draft_findings(
            draft, snapshot["turns"], snapshot.get("external_research")
        )
        source_ids = {
            turn_id
            for section in changed
            for paragraph in section.paragraphs
            for turn_id in paragraph.source_turn_ids
        }
        check_turns = [turn for turn in snapshot["turns"] if turn["item_id"] in source_ids]
        check_source = {
            "request": snapshot["instruction"],
            "changed_sections": draft.model_dump(mode="json")["sections"],
            "participant_turns": check_turns,
            "external_research": snapshot.get("external_research"),
            "outline": outline(current),
        }
        start = time.perf_counter()
        ai_check = await writer.check_article_changes(check_source)
        writer.usage.setdefault("stage_seconds", {})["check_changes"] = round(
            time.perf_counter() - start, 3
        )
        findings.extend(ai_check.findings)
        review_findings = findings
        serious = [f for f in findings if f.severity == "serious"]
        if serious:
            await progress("repair")
            repair_ids = {turn_id for item in serious for turn_id in item.source_turn_ids} | {
                turn_id
                for section in draft.sections
                for paragraph in section.paragraphs
                for turn_id in paragraph.source_turn_ids
            }
            repair_turns = [
                turn
                for turn in snapshot["turns"]
                if turn in selected_turns or turn["item_id"] in repair_ids
            ]
            repaired = await writer.repair_sections(
                {
                    "turns": repair_turns,
                    "article": snapshot["article"],
                    "external_research": snapshot.get("external_research"),
                    "surrounding_by_index": {
                        local_index: [
                            outline(current)[neighbor]
                            for neighbor in (current_index - 1, current_index + 1)
                            if 0 <= neighbor < len(current)
                        ]
                        for local_index, section_id in enumerate(changed_ids)
                        for current_index, section in enumerate(current)
                        if section["id"] == section_id
                    },
                },
                draft,
                serious,
            )
            wanted = {f.section_index for f in serious}
            returned = [r.section_index for r in repaired.repairs]
            if len(returned) != len(wanted) or set(returned) != wanted:
                repaired.repairs = []
            for repair in repaired.repairs:
                draft.sections[repair.section_index] = repair.section
                section_id = changed_ids[repair.section_index]
                current_index = next(i for i, s in enumerate(current) if s["id"] == section_id)
                generated = editor_sections(
                    GeneratedDraft(title="", sections=[repair.section], gaps=[]),
                    snapshot.get("external_research", {}).get("sources", []),
                )[0]
                current[current_index] = {
                    **current[current_index],
                    "title": generated["title"],
                    "editor_state": generated["editor_state"],
                }
            if repaired.repairs:
                check_editor_sections(current)
                remaining = validate_draft_findings(
                    draft, snapshot["turns"], snapshot.get("external_research")
                )
                repaired_ids = {
                    turn_id
                    for section in draft.sections
                    for paragraph in section.paragraphs
                    for turn_id in paragraph.source_turn_ids
                }
                recheck = await writer.check_article_changes(
                    {
                        **check_source,
                        "changed_sections": draft.model_dump(mode="json")["sections"],
                        "participant_turns": [
                            turn for turn in snapshot["turns"] if turn["item_id"] in repaired_ids
                        ],
                    }
                )
                remaining.extend(recheck.findings)
                review_findings = remaining

    if moved or plan.actions[0].kind == "full_rewrite":
        await progress("check")
        start = time.perf_counter()
        flow = await writer.check_article_flow(
            {
                "request": snapshot["instruction"],
                "outline": outline(current),
                "original_outline": outline(original),
            }
        )
        writer.usage.setdefault("stage_seconds", {})["check_flow"] = round(
            time.perf_counter() - start, 3
        )
        if moved and flow.findings:
            affected = {f.section_index for f in flow.findings}
            if any(index >= len(current) for index in affected):
                raise AskAIAccuracyError(["The flow review could not locate a transition."])
            await progress("repair")
            transition_edits = await writer.edit_article_transitions(
                {
                    "request": snapshot["instruction"],
                    "findings": [f.model_dump(mode="json") for f in flow.findings],
                    "targets": [
                        {
                            "section_index": index,
                            "heading": current[index]["title"],
                            "first_paragraph": section_text(current[index]).split("\n\n")[0],
                            "neighbors": [
                                outline(current)[i]
                                for i in (index - 1, index + 1)
                                if 0 <= i < len(current)
                            ],
                        }
                        for index in sorted(affected)
                    ],
                    "participant_turns": selected_turns,
                    "external_research": snapshot.get("external_research"),
                }
            )
            returned = [edit.section_index for edit in transition_edits.edits]
            if len(returned) != len(affected) or set(returned) != affected:
                flow_review_findings.extend(flow.findings)
                transition_edits.edits = []
            for edit in transition_edits.edits:
                original_section = current[edit.section_index]
                checked = GeneratedDraft(
                    title=snapshot["article"]["title"],
                    sections=[
                        GeneratedSection(
                            heading=original_section["title"],
                            paragraphs=[edit.paragraph],
                        )
                    ],
                    gaps=[],
                )
                local_issues = validate_draft_findings(
                    checked, snapshot["turns"], snapshot.get("external_research")
                )
                cited = set(edit.paragraph.source_turn_ids)
                ai_issues = await writer.check_article_changes(
                    {
                        "request": snapshot["instruction"],
                        "changed_sections": checked.model_dump(mode="json")["sections"],
                        "participant_turns": [
                            t for t in snapshot["turns"] if t["item_id"] in cited
                        ],
                        "external_research": snapshot.get("external_research"),
                        "outline": outline(current),
                    }
                )
                serious_transition = [
                    f.explanation
                    for f in local_issues + ai_issues.findings
                    if f.severity == "serious"
                ]
                if serious_transition:
                    flow_review_findings.extend(
                        finding.model_copy(update={"section_index": edit.section_index})
                        for finding in local_issues + ai_issues.findings
                    )
                    continue
                generated = editor_sections(
                    checked,
                    snapshot.get("external_research", {}).get("sources", []),
                )[0]
                old_document = json.loads(original_section["editor_state"])
                new_document = json.loads(generated["editor_state"])
                if not old_document["root"]["children"]:
                    raise AskAIAccuracyError(["The transition has no paragraph to edit."])
                old_document["root"]["children"][0] = new_document["root"]["children"][0]
                current[edit.section_index] = {
                    **original_section,
                    "editor_state": json.dumps(old_document),
                }
                changed_ids.append(original_section["id"])
            check_editor_sections(current)
            summary = (summary + " Adjusted the affected transitions.").strip()
        elif any(f.severity == "serious" for f in flow.findings):
            flow_review_findings.extend(flow.findings)

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
    if review_findings or flow_review_findings:
        remapped: list[DraftFinding] = []
        for finding in review_findings:
            if finding.section_index >= len(changed_ids):
                continue
            section_id = changed_ids[finding.section_index]
            current_index = next(
                (i for i, section in enumerate(current) if section["id"] == section_id),
                finding.section_index,
            )
            remapped.append(finding.model_copy(update={"section_index": current_index}))
        anchored = anchor_findings(remapped, current)
        anchored.extend(anchor_findings(flow_review_findings, current))
        candidate["findings"] = [
            finding.model_dump(mode="json")
            for finding in anchored
        ]
    return current, candidate, snapshot
