"""Source fidelity and immutable consultant snapshot tests."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.prompts.consultant import DIRECT_DRAFT_INSTRUCTION, PROMPT_VERSION, WRITING_POLICY
from app.schemas.auth import RegisterRequest
from app.schemas.consultant import (
    ConsultantArticleInput,
    DraftCheck,
    DraftFinding,
    GeneratedDraft,
    SectionRepairs,
)
from app.schemas.draft import ArticleDraftUpdate
from app.services.consultant_service import effective_turns
from app.services.consultant_writer import (
    ConsultantWriter,
    compact_snapshot,
    direct_draft_source,
    editor_sections,
    sanitize_draft_quotes,
    validate_draft,
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


def test_consultant_fidelity_policy_has_a_new_cache_version() -> None:
    assert PROMPT_VERSION == "consultant_direct_draft_v5"
    instruction = " ".join(DIRECT_DRAFT_INSTRUCTION.split())
    assert "do not add or replace claims, experiences" in instruction
    assert "properly structured blog" in instruction


@pytest.mark.asyncio
async def test_consultant_writer_uses_medium_reasoning_for_parse_and_research() -> None:
    parse = AsyncMock(
        return_value=SimpleNamespace(
            output_parsed=DraftCheck(findings=[]), usage=None, _request_id="parse-request"
        )
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            output=[], output_text="No public evidence needed.", usage=None,
            _request_id="research-request",
        )
    )
    writer = object.__new__(ConsultantWriter)
    writer.client = SimpleNamespace(responses=SimpleNamespace(parse=parse, create=create))
    writer.model = "gpt-5.6-sol"
    writer.reasoning_effort = "medium"
    writer.metrics = None
    writer.usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "api_call_counts": {},
        "api_attempt_counts": {},
        "stage_seconds": {},
        "model_calls": [],
        "rate_limit_wait_seconds": 0,
    }

    await writer.parse(DraftCheck, "Check the draft.", {}, operation_name="check")
    await writer.research({"instruction": "Find sources"})

    assert parse.await_args.kwargs["reasoning"] == {"effort": "medium"}
    assert create.await_args.kwargs["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_write_uses_only_direct_draft_instruction_and_transcript_source() -> None:
    captured: dict[str, Any] = {}

    async def parse_response(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=draft(), usage=None, _request_id="write-request")

    writer = object.__new__(ConsultantWriter)
    writer.client = SimpleNamespace(responses=SimpleNamespace(parse=parse_response))
    writer.model = "gpt-5.6-sol"
    writer.reasoning_effort = "medium"
    writer.metrics = None
    writer.usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "api_call_counts": {},
        "api_attempt_counts": {},
        "stage_seconds": {},
        "model_calls": [],
        "rate_limit_wait_seconds": 0,
    }

    await writer.write(
        {
            "article": {
                "title": "Handoffs",
                "description": "Private notes",
                "audience": "Founders",
                "takeaway": "A separate takeaway",
                "length": "standard",
            },
            "turns": TURNS,
            "instruction": "initial",
            "request_kind": "generation",
            "base_sections": None,
        }
    )

    request = captured
    assert request["instructions"] == DIRECT_DRAFT_INSTRUCTION
    assert WRITING_POLICY not in request["instructions"]
    assert json.loads(request["input"]) == {
        "article": {"title": "Handoffs", "audience": "Founders", "length": "standard"},
        "turns": TURNS,
    }


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


def test_direct_draft_source_contains_only_transcript_and_article_basics() -> None:
    source = direct_draft_source(
        {
            "article": {
                "title": "Handoffs",
                "description": "Do not send this",
                "audience": "Founders",
                "takeaway": "Do not send this either",
                "length": "standard",
            },
            "turns": TURNS,
            "instruction": "initial",
            "request_kind": "generation",
            "external_research": {"findings": "Do not send initial research"},
            "base_sections": None,
        }
    )

    assert source == {
        "article": {"title": "Handoffs", "audience": "Founders", "length": "standard"},
        "turns": TURNS,
    }


def test_direct_draft_source_includes_current_draft_for_rewrites() -> None:
    source = direct_draft_source(
        {
            "article": {"title": "Handoffs", "audience": "Founders", "length": "short"},
            "turns": TURNS,
            "instruction": "closer",
            "base_sections": [{"title": "Opening", "text": "Existing copy"}],
        }
    )

    assert source["instruction"] == "closer"
    assert source["base_sections"] == [{"title": "Opening", "text": "Existing copy"}]


def test_direct_draft_source_keeps_explicit_ask_ai_research() -> None:
    research = {"findings": "Verified public example", "sources": []}
    source = direct_draft_source(
        {
            "article": {"title": "Handoffs", "audience": "Founders", "length": "standard"},
            "turns": TURNS,
            "request_kind": "ask_ai",
            "instruction": "Rewrite with a sourced example",
            "base_sections": [{"title": "Opening", "text": "Existing copy"}],
            "external_research": research,
        }
    )

    assert source["external_research"] == research


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


def test_generated_draft_with_anchors_can_be_serialized_for_job_checkpoint() -> None:
    candidate = draft("A supported paragraph.")
    candidate.sections[0].section_id = uuid4()
    candidate.sections[0].paragraphs[0].paragraph_id = uuid4()

    payload = candidate.model_dump(mode="json")

    json.dumps(payload)
    assert isinstance(payload["sections"][0]["section_id"], str)
    assert isinstance(payload["sections"][0]["paragraphs"][0]["paragraph_id"], str)


def test_consultant_voice_instructions_do_not_use_client_invitation_language() -> None:
    c = ConsultantVoiceContext("Jo", "Handoffs", "Broken handoffs", ["Founders"], [])
    prompt = build_interview_workflow_instructions(c)
    assert "automatically creates a draft" in prompt
    assert "at most two optional follow-ups" in prompt
    assert "next natural pause" in prompt
    assert "cost schedule" in prompt
    assert "the client's team" not in prompt
    assert "current interview as the only source" in WRITING_POLICY
    assert '"recognises patterns"' in WRITING_POLICY
    assert 'does not mean "makes predictions"' in WRITING_POLICY
    assert "unspecified group does not mean engineering students" in WRITING_POLICY


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


async def test_checker_records_api_call_counts_and_tokens() -> None:
    response = SimpleNamespace(
        output_parsed=DraftCheck(findings=[]),
        usage=SimpleNamespace(input_tokens=120, output_tokens=18),
    )
    parse = AsyncMock(return_value=response)
    writer = object.__new__(ConsultantWriter)
    writer.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    writer.model = "test-model"
    writer.usage = {"input_tokens": 0, "output_tokens": 0}

    result = await writer.check({"turns": TURNS}, draft())

    assert result.findings == []
    assert writer.usage["api_call_counts"] == {"check_full": 1}
    assert writer.usage["api_attempt_counts"] == {"check_full": 1}
    assert writer.usage["input_tokens"] == 120
    assert writer.usage["output_tokens"] == 18


async def test_section_repair_sends_only_affected_content_and_nearby_context() -> None:
    candidate = draft()
    candidate.sections.append(
        candidate.sections[0].model_copy(update={"heading": "Next steps"})
    )
    finding = DraftFinding(
        severity="serious",
        category="unsupported_fact",
        section_index=0,
        paragraph_index=0,
        explanation="Unsupported claim.",
        source_turn_ids=["a"],
    )
    writer = object.__new__(ConsultantWriter)
    writer.parse = AsyncMock(return_value=SectionRepairs(repairs=[{
        "section_index": 0,
        "section": candidate.sections[0],
    }]))

    await writer.repair_sections(
        {"turns": TURNS, "article": {"title": "Handoffs"}}, candidate, [finding]
    )

    payload = writer.parse.call_args.args[2]
    assert len(payload["affected_sections"]) == 1
    assert payload["affected_sections"][0]["section_index"] == 0
    affected = payload["affected_sections"][0]
    assert [turn["item_id"] for turn in affected["participant_turns"]] == ["a"]
    assert affected["surrounding_sections"][0]["heading"] == "Next steps"
    assert "candidate" not in payload
