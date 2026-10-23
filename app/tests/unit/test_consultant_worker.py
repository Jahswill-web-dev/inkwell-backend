"""Consultant worker process startup tests."""

import copy
from collections.abc import Coroutine
from typing import Any

import pytest

from app.schemas.consultant import DraftCheck, GeneratedDraft, SectionRepairs
from app.workers import consultant


def test_worker_uses_selector_event_loop_policy_on_windows(monkeypatch: Any) -> None:
    selected: list[object] = []
    policy = object()

    async def fake_main() -> None:
        return None

    def fake_run(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()

    monkeypatch.setattr(consultant.sys, "platform", "win32")
    monkeypatch.setattr(consultant.asyncio, "WindowsSelectorEventLoopPolicy", lambda: policy)
    monkeypatch.setattr(consultant.asyncio, "set_event_loop_policy", selected.append)
    monkeypatch.setattr(consultant.asyncio, "run", fake_run)
    monkeypatch.setattr(consultant, "main", fake_main)

    consultant.run()

    assert selected == [policy]


def test_source_fidelity_message_shows_bounded_checker_feedback() -> None:
    message = consultant.source_fidelity_message(
        [
            "  This claim is not supported\nby the interview.  ",
            "This claim is not supported by the interview.",
            "The example is attributed to the consultant without evidence.",
            "The quotation is not verbatim.",
            "A statistic lacks a source.",
            "A fifth distinct issue should not appear.",
        ]
    )

    assert "1. This claim is not supported by the interview." in message
    assert message.count("This claim is not supported by the interview.") == 1
    assert "The quotation is not verbatim." in message
    assert "A fifth distinct issue" not in message
    assert message.endswith("Please review these points before retrying.")


TURNS = [
    {"item_id": "t1", "speaker": "participant", "text": "Fix the handoff first."},
    {"item_id": "t2", "speaker": "participant", "text": "Give the next step one owner."},
]


def example_draft(first_text: str = "Fix the handoff first.") -> GeneratedDraft:
    return GeneratedDraft(
        title="Handoffs",
        sections=[
            {"heading": "Opening", "paragraphs": [{
                "text": first_text, "source_turn_ids": ["t1"], "direct_quotes": [],
            }]},
            {"heading": "Ownership", "paragraphs": [{
                "text": "Give the next step one owner.",
                "source_turn_ids": ["t2"], "direct_quotes": [],
            }]},
        ],
        gaps=[],
    )


class ReviewWriter:
    def __init__(self, first_check: DraftCheck, repaired_text: str = "Fix the handoff first."):
        self.first_check = first_check
        self.repaired_text = repaired_text
        self.calls: list[str] = []
        self.usage: dict[str, Any] = {"stage_seconds": {}}

    async def check(self, snapshot: dict[str, Any], draft: GeneratedDraft) -> DraftCheck:
        self.calls.append("check_full")
        return self.first_check

    async def repair_sections(
        self, snapshot: dict[str, Any], draft: GeneratedDraft, findings: list[Any]
    ) -> SectionRepairs:
        self.calls.append("repair_sections")
        section = draft.sections[0].model_copy(deep=True)
        section.paragraphs[0].text = self.repaired_text
        return SectionRepairs(repairs=[{"section_index": 0, "section": section}])

    async def check_repaired_sections(
        self, snapshot: dict[str, Any], draft: GeneratedDraft, indices: set[int]
    ) -> DraftCheck:
        self.calls.append("check_sections")
        assert indices == {0}
        return DraftCheck(findings=[])


def finding(severity: str, category: str, explanation: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "section_index": 0,
        "paragraph_index": 0,
        "explanation": explanation,
        "source_turn_ids": ["t1"],
    }


def test_accuracy_finding_cannot_be_downgraded_to_style() -> None:
    check = DraftCheck(findings=[
        finding("style", "invented_experience", "This experience was not in the interview.")
    ])
    assert check.findings[0].severity == "serious"
    public_fact = DraftCheck(findings=[
        finding("style", "unsupported_public_fact", "The public claim has no source.")
    ])
    assert public_fact.findings[0].severity == "serious"


@pytest.mark.asyncio
async def test_clean_initial_draft_uses_one_full_review() -> None:
    writer = ReviewWriter(DraftCheck(findings=[]))
    result, suggestions = await consultant.review_initial_draft(
        writer, {"turns": TURNS}, example_draft()
    )
    assert result.sections[0].paragraphs[0].text == "Fix the handoff first."
    assert suggestions == []
    assert writer.calls == ["check_full"]
    assert "check_full" in writer.usage["stage_seconds"]


@pytest.mark.asyncio
async def test_style_only_does_not_repair_or_block() -> None:
    writer = ReviewWriter(DraftCheck(findings=[
        finding("style", "wording", "Try a shorter opening sentence.")
    ]))
    result, suggestions = await consultant.review_initial_draft(
        writer, {"turns": TURNS}, example_draft()
    )
    assert result.sections[0].paragraphs[0].text == "Fix the handoff first."
    assert [item.explanation for item in suggestions] == ["Try a shorter opening sentence."]
    assert writer.calls == ["check_full"]


@pytest.mark.asyncio
async def test_serious_finding_repairs_one_section_and_preserves_the_other() -> None:
    writer = ReviewWriter(DraftCheck(findings=[
        finding("serious", "unsupported_fact", "No interview support for 50% growth.")
    ]))
    original = example_draft("Revenue grew 50%.")
    untouched = copy.deepcopy(original.sections[1].model_dump())
    result, suggestions = await consultant.review_initial_draft(
        writer, {"turns": TURNS}, original
    )
    assert result.sections[0].paragraphs[0].text == "Fix the handoff first."
    assert result.sections[1].model_dump() == untouched
    assert original.sections[0].paragraphs[0].text == "Revenue grew 50%."
    assert suggestions == []
    assert writer.calls == ["check_full", "repair_sections", "check_sections"]


@pytest.mark.asyncio
async def test_unresolved_serious_finding_returns_reviewable_draft() -> None:
    writer = ReviewWriter(
        DraftCheck(findings=[
            finding("serious", "unsupported_fact", "No interview support for 50% growth.")
        ]),
        repaired_text="Revenue grew 50%.",
    )
    draft, findings = await consultant.review_initial_draft(
        writer, {"turns": TURNS}, example_draft("Revenue grew 50%.")
    )
    assert draft.sections[0].paragraphs[0].text == "Revenue grew 50%."
    assert findings[0].classification == "unsupported"
    assert "Numerical claim" in findings[0].explanation
    assert writer.calls == ["check_full", "repair_sections", "check_sections"]
