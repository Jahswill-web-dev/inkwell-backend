"""Source fidelity and immutable consultant snapshot tests."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.prompts.consultant import WRITING_POLICY
from app.schemas.auth import RegisterRequest
from app.schemas.consultant import ConsultantArticleInput, Evidence, GeneratedDraft
from app.schemas.draft import ArticleDraftUpdate
from app.services.consultant_service import effective_turns
from app.services.consultant_writer import (
    ConsultantWriter,
    compact_snapshot,
    editor_sections,
    sanitize_draft_quotes,
    sanitize_evidence,
    validate_draft,
    validate_evidence,
)
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
    generated = draft()
    generated.sections[0].heading = "1. The problem"
    sections = editor_sections(generated)
    ArticleDraftUpdate(sections=sections)
    assert "source_turn_ids" not in json.dumps(sections)
    assert "expensive confusion" in sections[0]["editor_state"]
    assert sections[0]["title"] == "The problem"


def test_public_examples_require_a_retrieved_source_and_render_a_link() -> None:
    generated = draft("A published case study reported a 20% improvement.", ids=[])
    generated.sections[0].paragraphs[0].external_source_ids = ["s1"]
    research = {
        "findings": "A published case study reported a 20% improvement.",
        "sources": [{"id": "s1", "title": "Case study", "url": "https://example.org/study"}],
    }
    assert validate_draft(generated, TURNS)
    assert not validate_draft(generated, TURNS, research)
    sections = editor_sections(generated, research["sources"])
    ArticleDraftUpdate(sections=sections)
    editor = json.loads(sections[0]["editor_state"])
    link = editor["root"]["children"][0]["children"][-1]
    assert link["type"] == "link"
    assert link["url"] == "https://example.org/study"


async def test_research_uses_provider_citation_urls_only() -> None:
    annotation = SimpleNamespace(model_dump=lambda: {
        "type": "url_citation", "url": "https://example.org/study", "title": "Case study"
    })
    response = SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(
            annotations=[annotation]
        )])],
        output_text="A documented example, with a citation.",
        usage=None,
    )
    create = AsyncMock(return_value=response)
    writer = object.__new__(ConsultantWriter)
    writer.client = SimpleNamespace(responses=SimpleNamespace(create=create))
    writer.model = "test-model"
    writer.usage = {"input_tokens": 0, "output_tokens": 0}

    result = await writer.research(
        {"article": {"title": "Handoffs"}, "instruction": "Find sources"}
    )

    assert result["sources"] == [{
        "id": "s1", "title": "Case study", "url": "https://example.org/study"
    }]
    assert create.call_args.kwargs["tools"] == [{"type": "web_search"}]


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


def test_invalid_model_evidence_is_removed_before_generation() -> None:
    evidence = Evidence(
        argument="Handoffs",
        claims=[
            {"statement": "Supported", "turn_ids": ["a", "b"]},
            {"statement": "Interviewer only", "turn_ids": ["b"]},
        ],
        outline=["The problem"],
        gaps=[],
        voice={"excerpts": ["expensive confusion", "increase revenue by 50%"]},
    )

    cleaned = sanitize_evidence(evidence, TURNS)

    assert [claim.statement for claim in cleaned.claims] == ["Supported"]
    assert cleaned.claims[0].turn_ids == ["a"]
    assert cleaned.voice.excerpts == ["expensive confusion"]
    validate_evidence(cleaned, TURNS)


def test_unsupported_quote_styling_is_converted_to_paraphrase() -> None:
    candidate = draft(
        'Teams call this “cheap confusion”, but I call it expensive confusion.',
        quotes=["cheap confusion"],
    )

    cleaned = sanitize_draft_quotes(candidate, TURNS)

    paragraph = cleaned.sections[0].paragraphs[0]
    assert "cheap confusion" in paragraph.text
    assert "“cheap confusion”" not in paragraph.text
    assert paragraph.direct_quotes == []


def test_consultant_voice_instructions_do_not_use_client_invitation_language() -> None:
    c = ConsultantVoiceContext("Jo", "Handoffs", "Broken handoffs", ["Founders"], [])
    prompt = build_interview_workflow_instructions(c)
    assert "automatically creates a draft" in prompt
    assert "at most two follow-ups" in prompt
    assert "the client's team" not in prompt
    assert "current interview as the only source" in WRITING_POLICY


def test_minimal_article_requires_no_client_or_brief() -> None:
    a = ConsultantArticleInput(title="Handoffs", description="Why ownership matters")
    assert a.length == "standard"
    assert a.audience == ""


def test_model_snapshot_uses_short_stable_ids_without_changing_source() -> None:
    source = {
        "turns": TURNS,
        "transcript_revisions": {"interview": 3},
        "article": {"title": "Handoffs", "length": "standard"},
        "base_sections": None,
    }

    compact = compact_snapshot(source)

    assert [turn["item_id"] for turn in compact["turns"]] == ["t1", "t2"]
    assert "transcript_revisions" not in compact
    assert source["turns"] == TURNS
