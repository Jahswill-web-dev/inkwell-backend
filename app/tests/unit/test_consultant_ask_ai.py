import copy
import json
from uuid import uuid4

import pytest

from app.schemas.consultant import (
    ArticleEditPlan,
    ArticleFlowCheck,
    ArticleSectionEdits,
    ArticleTransitionEdits,
    DraftCheck,
    Evidence,
    SectionRepairs,
)
from app.services.consultant_ask_ai import (
    AskAIAccuracyError,
    AskAIClarification,
    prepare_ask_ai,
)


def section(title: str, text: str) -> dict:
    return {
        "id": str(uuid4()),
        "outline_section_id": None,
        "title": title,
        "goal": "Keep this goal",
        "checklist": [],
        "editor_state": json.dumps(
            {
                "root": {
                    "type": "root",
                    "version": 1,
                    "children": [
                        {
                            "type": "paragraph",
                            "version": 1,
                            "children": [{"type": "text", "version": 1, "text": text}],
                        }
                    ],
                }
            }
        ),
    }


TURNS = [
    {"item_id": "t1", "speaker": "participant", "text": "Start with the costly handoff."},
    {"item_id": "t2", "speaker": "participant", "text": "Give the next step one owner."},
]


class Writer:
    def __init__(self, plan: ArticleEditPlan, edits: ArticleSectionEdits | None = None):
        self.plan = plan
        self.edits = edits
        self.usage = {"stage_seconds": {}}
        self.calls = []

    async def plan_article_edit(self, source):
        self.calls.append("plan")
        return self.plan

    async def edit_article_sections(self, source):
        self.calls.append("edit")
        self.input = source
        return self.edits

    async def check_article_changes(self, source):
        self.calls.append("check")
        return DraftCheck(findings=[])

    async def check_article_flow(self, source):
        self.calls.append("flow")
        return ArticleFlowCheck(findings=[])


def context(sections):
    return {
        "article": {"title": "Handoffs"},
        "instruction": "Improve my article",
        "base_sections": sections,
        "turns": TURNS,
    }


def evidence():
    return Evidence(
        argument="Own the handoff",
        claims=[],
        outline=[],
        gaps=[],
        voice={"excerpts": ["Start with the costly handoff."]},
    )


async def progress(stage):
    return None


@pytest.mark.asyncio
async def test_opening_edit_preserves_other_sections_exactly():
    sections = [section("Opening", "Old opening"), section("Advice", "Keep this text")]
    untouched = copy.deepcopy(sections[1])
    writer = Writer(
        ArticleEditPlan(
            actions=[
                {
                    "kind": "text_edit",
                    "target_section_id": sections[0]["id"],
                    "instruction": "Make the opening hook better",
                }
            ]
        ),
        ArticleSectionEdits(
            repairs=[
                {
                    "section_index": 0,
                    "section": {
                        "heading": "Opening",
                        "paragraphs": [
                            {
                                "text": "Start with the costly handoff.",
                                "source_turn_ids": ["t1"],
                                "direct_quotes": [],
                            }
                        ],
                    },
                }
            ],
            summary="Sharpened the opening.",
        ),
    )
    result, candidate, _ = await prepare_ask_ai(writer, context(sections), evidence(), progress)
    assert result[1] == untouched
    assert result[0]["id"] == sections[0]["id"]
    assert candidate["summary"] == "Sharpened the opening."
    assert writer.calls == ["plan", "edit", "check"]
    assert [turn["item_id"] for turn in writer.input["participant_turns"]] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_move_reuses_section_objects_without_writing():
    sections = [section("Background", "First"), section("Practical advice", "Second")]
    writer = Writer(
        ArticleEditPlan(
            actions=[
                {
                    "kind": "section_move",
                    "target_section_id": sections[1]["id"],
                    "destination_section_id": sections[0]["id"],
                    "position": "before",
                    "instruction": "Move advice before background",
                }
            ]
        )
    )
    result, _, _ = await prepare_ask_ai(writer, context(sections), evidence(), progress)
    assert result == [sections[1], sections[0]]
    assert writer.calls == ["plan", "flow"]


@pytest.mark.asyncio
async def test_conclusion_addition_and_opening_edit_in_one_request():
    sections = [
        section("Opening", "Old opening"),
        section("Background", "Keep the background"),
        section("Conclusion", "Old conclusion"),
    ]
    writer = Writer(
        ArticleEditPlan(
            actions=[
                {
                    "kind": "text_edit",
                    "target_section_id": sections[0]["id"],
                    "instruction": "Improve the hook",
                },
                {
                    "kind": "addition",
                    "target_section_id": sections[2]["id"],
                    "instruction": "Add the one-owner takeaway",
                },
            ]
        ),
        ArticleSectionEdits(
            repairs=[
                {
                    "section_index": 0,
                    "section": {
                        "heading": "Opening",
                        "paragraphs": [
                            {
                                "text": "Start with the costly handoff.",
                                "source_turn_ids": ["t1"],
                                "direct_quotes": [],
                            }
                        ],
                    },
                },
                {
                    "section_index": 2,
                    "section": {
                        "heading": "Conclusion",
                        "paragraphs": [
                            {
                                "text": "Give the next step one owner.",
                                "source_turn_ids": ["t2"],
                                "direct_quotes": [],
                            }
                        ],
                    },
                },
            ]
        ),
    )
    result, _, _ = await prepare_ask_ai(writer, context(sections), evidence(), progress)
    assert result[1] == sections[1]
    assert result[0]["id"] == sections[0]["id"]
    assert result[2]["id"] == sections[2]["id"]
    assert [target["section_index"] for target in writer.input["targets"]] == [0, 2]
    assert writer.calls == ["plan", "edit", "check"]


@pytest.mark.asyncio
async def test_researched_fact_retains_a_clickable_citation():
    sections = [section("Conclusion", "Old conclusion")]
    writer = Writer(
        ArticleEditPlan(
            actions=[
                {
                    "kind": "addition",
                    "target_section_id": sections[0]["id"],
                    "instruction": "Add a sourced public example",
                }
            ]
        ),
        ArticleSectionEdits(
            repairs=[
                {
                    "section_index": 0,
                    "section": {
                        "heading": "Conclusion",
                        "paragraphs": [
                            {
                                "text": "The study reports 25%.",
                                "source_turn_ids": [],
                                "external_source_ids": ["s1"],
                                "direct_quotes": [],
                            }
                        ],
                    },
                }
            ]
        ),
    )

    async def research(source):
        writer.calls.append("research")
        assert len(source["research_sections"]) == 1
        return {
            "findings": "The study reports 25%.",
            "sources": [
                {
                    "id": "s1",
                    "title": "Study",
                    "url": "https://example.org/study",
                }
            ],
        }

    writer.research = research
    snapshot = context(sections)
    snapshot["instruction"] = "Add a sourced public example"
    result, _, updated = await prepare_ask_ai(writer, snapshot, evidence(), progress)
    assert updated["external_research"]["sources"][0]["id"] == "s1"
    assert "https://example.org/study" in result[0]["editor_state"]
    assert writer.calls == ["plan", "research", "edit", "check"]


@pytest.mark.asyncio
async def test_move_repairs_only_affected_transition_paragraph():
    sections = [
        section("Background", "The previous advice explains this."),
        section("Advice", "Give the next step one owner."),
    ]
    document = json.loads(sections[0]["editor_state"])
    second = copy.deepcopy(document["root"]["children"][0])
    second["children"][0]["text"] = "Preserve this formatted paragraph."
    second["custom_format"] = "keep"
    document["root"]["children"].append(second)
    sections[0]["editor_state"] = json.dumps(document)
    writer = Writer(
        ArticleEditPlan(
            actions=[
                {
                    "kind": "section_move",
                    "target_section_id": sections[1]["id"],
                    "destination_section_id": sections[0]["id"],
                    "position": "before",
                    "instruction": "Move advice before background",
                }
            ]
        )
    )

    async def flow(source):
        writer.calls.append("flow")
        return ArticleFlowCheck(
            findings=[
                {
                    "severity": "style",
                    "category": "readability",
                    "section_index": 1,
                    "explanation": "Opening transition points to the old section order.",
                }
            ]
        )

    async def transition(source):
        writer.calls.append("transition")
        return ArticleTransitionEdits(
            edits=[
                {
                    "section_index": 1,
                    "paragraph": {
                        "text": "Start with the costly handoff.",
                        "source_turn_ids": ["t1"],
                        "direct_quotes": [],
                    },
                }
            ]
        )

    writer.check_article_flow = flow
    writer.edit_article_transitions = transition
    result, _, _ = await prepare_ask_ai(writer, context(sections), evidence(), progress)
    assert result[0] == sections[1]
    assert result[1]["id"] == sections[0]["id"]
    updated = json.loads(result[1]["editor_state"])
    assert updated["root"]["children"][1] == second
    assert writer.calls == ["plan", "flow", "transition", "check"]


@pytest.mark.asyncio
async def test_unclear_request_returns_clarification_without_edit():
    sections = [section("Opening", "One")]
    writer = Writer(
        ArticleEditPlan(clarification_question="Which point should I add to the conclusion?")
    )
    with pytest.raises(AskAIClarification, match="Which point"):
        await prepare_ask_ai(writer, context(sections), evidence(), progress)
    assert writer.calls == ["plan"]


@pytest.mark.asyncio
async def test_invented_number_blocks_after_one_repair():
    sections = [section("Conclusion", "Old")]
    writer = Writer(
        ArticleEditPlan(
            actions=[
                {
                    "kind": "addition",
                    "target_section_id": sections[0]["id"],
                    "instruction": "Add a takeaway",
                }
            ]
        ),
        ArticleSectionEdits(
            repairs=[
                {
                    "section_index": 0,
                    "section": {
                        "heading": "Conclusion",
                        "paragraphs": [
                            {
                                "text": "Results improved by 50%.",
                                "source_turn_ids": ["t2"],
                                "direct_quotes": [],
                            }
                        ],
                    },
                }
            ]
        ),
    )

    async def repair(snapshot, draft, findings):
        writer.calls.append("repair")
        return SectionRepairs(repairs=[{"section_index": 0, "section": draft.sections[0]}])

    writer.repair_sections = repair
    with pytest.raises(AskAIAccuracyError, match="Numerical claim"):
        await prepare_ask_ai(writer, context(sections), evidence(), progress)
    assert writer.calls == ["plan", "edit", "check", "repair", "check"]
