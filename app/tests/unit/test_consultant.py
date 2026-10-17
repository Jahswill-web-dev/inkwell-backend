"""Source fidelity and immutable consultant snapshot tests."""

import json
from typing import Any

import pytest

from app.prompts.consultant import WRITING_POLICY
from app.schemas.auth import RegisterRequest
from app.schemas.consultant import ConsultantArticleInput, Evidence, GeneratedDraft
from app.schemas.draft import ArticleDraftUpdate
from app.services.consultant_service import effective_turns
from app.services.consultant_writer import editor_sections, validate_draft, validate_evidence
from app.services.interview_context import ConsultantVoiceContext
from app.services.openai_realtime import build_interview_workflow_instructions

TURNS: list[dict[str, Any]] = [
    {
        "item_id": "a",
        "speaker": "participant",
        "text": (
            "I call that expensive confusion. We had 5 dashboards, "
            "but sometimes nobody owned the next step."
        ),
    },
    {"item_id": "b", "speaker": "interviewer", "text": "Did that increase revenue by 50%?"},
]


def draft(
    text: str = "I call that expensive confusion.",
    ids: list[str] | None = None,
    quotes: list[str] | None = None,
) -> GeneratedDraft:
    return GeneratedDraft(
        title="Handoffs",
        sections=[
            {
                "heading": "The problem",
                "paragraphs": [
                    {
                        "text": text,
                        "source_turn_ids": ["a"] if ids is None else ids,
                        "direct_quotes": [] if quotes is None else quotes,
                    }
                ],
            }
        ],
        gaps=[],
    )


def test_registration_defaults_preserve_marketing() -> None:
    data = dict(email="writer@example.com", username="writer", password="long-password")
    assert RegisterRequest(**data).experience == "marketing"
    assert RegisterRequest(**data, experience="consultant").experience == "consultant"
    with pytest.raises(ValueError):
        RegisterRequest(**data, experience="admin")


def test_corrections_are_separate_and_exclusions_remove_evidence() -> None:
    original = json.loads(json.dumps(TURNS))
    result = effective_turns(
        TURNS, {"a": {"text": "Corrected explanation", "excluded": False}, "b": {"excluded": True}}
    )
    assert result == [{"item_id": "a", "speaker": "participant", "text": "Corrected explanation"}]
    assert TURNS == original


def test_interviewer_cannot_be_a_factual_source() -> None:
    assert validate_draft(draft(ids=["b"]), TURNS)
    assert not validate_draft(draft(), TURNS)


def test_numerical_invention_and_changed_quotes_are_rejected() -> None:
    assert validate_draft(draft("Revenue increased 50%."), TURNS)
    assert validate_draft(draft('I call that "cheap confusion".'), TURNS)
    assert validate_draft(draft(quotes=["cheap confusion"]), TURNS)
    assert not validate_draft(draft("We had 5 dashboards."), TURNS)


def test_repeated_paragraphs_are_rejected() -> None:
    d = draft()
    d.sections[0].paragraphs.append(d.sections[0].paragraphs[0])
    assert "Empty or repeated paragraph" in validate_draft(d, TURNS)


def test_editor_content_is_compatible_and_does_not_expose_source_metadata() -> None:
    sections = editor_sections(draft())
    ArticleDraftUpdate(sections=sections)
    assert "source_turn_ids" not in json.dumps(sections)
    assert "expensive confusion" in sections[0]["editor_state"]


def test_voice_excerpts_must_be_verbatim_participant_speech() -> None:
    e = Evidence(
        argument="Handoffs",
        claims=[{"statement": "Handoffs matter", "turn_ids": ["a"]}],
        outline=["The problem"],
        gaps=[],
        voice={"excerpts": ["expensive confusion"]},
    )
    validate_evidence(e, TURNS)
    e.voice.excerpts = ["increase revenue by 50%"]
    with pytest.raises(ValueError):
        validate_evidence(e, TURNS)


def test_consultant_voice_instructions_do_not_use_client_invitation_language() -> None:
    c = ConsultantVoiceContext("Jo", "Handoffs", "Broken handoffs", ["Founders"], [])
    prompt = build_interview_workflow_instructions(c)
    assert "automatically creates a draft" in prompt
    assert "at most two follow-ups" in prompt
    assert "the client's team" not in prompt
    assert "STYLE ONLY, NEVER FACTS" in WRITING_POLICY


def test_minimal_article_requires_no_client_or_brief() -> None:
    a = ConsultantArticleInput(title="Handoffs", description="Why ownership matters")
    assert a.length == "standard"
    assert a.audience == ""
