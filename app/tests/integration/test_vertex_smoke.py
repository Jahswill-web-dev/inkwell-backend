import os

import pytest

from app.core.config import Settings
from app.services.ai_service import (
    BriefSource,
    OutlineSource,
    VertexGeminiBriefGenerator,
    VertexGeminiOutlineGenerator,
)
from app.services.section_interview_ai import VertexGeminiSectionInterviewGenerator

pytestmark = pytest.mark.vertex


@pytest.mark.skipif(
    os.getenv("RUN_VERTEX_SMOKE_TEST") != "true",
    reason="Set RUN_VERTEX_SMOKE_TEST=true to call Vertex AI",
)
async def test_vertex_generates_a_structured_brief(settings: Settings) -> None:
    if settings.vertex_project_id is None:
        pytest.fail("VERTEX_PROJECT_ID is required for the Vertex smoke test")
    generator = VertexGeminiBriefGenerator(settings)
    try:
        result = await generator.generate(
            BriefSource(
                working_title="How small teams can publish consistently",
                notes="Small teams struggle with unclear ownership and irregular review cycles.",
                target_audience=["Independent writers", "Small content teams"],
                article_goal="educate_with_practical_guidance",
            )
        )
    finally:
        await generator.close()

    assert result.brief.core_angle
    assert result.model_id == settings.vertex_model_id


@pytest.mark.skipif(
    os.getenv("RUN_VERTEX_SMOKE_TEST") != "true",
    reason="Set RUN_VERTEX_SMOKE_TEST=true to call Vertex AI",
)
async def test_vertex_generates_a_structured_outline(settings: Settings) -> None:
    if settings.vertex_project_id is None:
        pytest.fail("VERTEX_PROJECT_ID is required for the Vertex smoke test")
    generator = VertexGeminiOutlineGenerator(settings)
    try:
        result = await generator.generate(
            OutlineSource(
                summary="A practical guide to consistent publishing.",
                core_angle="Consistency comes from clear ownership and review cycles.",
                audience_insights=["Small teams need a lightweight process"],
                tone_and_style="Clear and pragmatic",
                key_takeaways=["Assign ownership", "Plan reviews", "Measure consistency"],
                evidence_gaps=[],
                call_to_action="Create a publishing checklist",
            )
        )
    finally:
        await generator.close()

    assert result.outline.sections
    assert result.model_id == settings.vertex_model_id


@pytest.mark.skipif(
    os.getenv("RUN_VERTEX_SMOKE_TEST") != "true",
    reason="Set RUN_VERTEX_SMOKE_TEST=true to call Vertex AI",
)
async def test_vertex_generates_a_structured_section_draft(settings: Settings) -> None:
    if settings.vertex_project_id is None:
        pytest.fail("VERTEX_PROJECT_ID is required for the Vertex smoke test")
    generator = VertexGeminiSectionInterviewGenerator(settings)
    try:
        result = await generator.generate_direct_draft(
            {
                "selected_section": {
                    "title": "Define clear publishing ownership",
                    "goal": "Give small teams a practical ownership process",
                    "outline_key_points": ["Assign one owner to each publishing stage"],
                    "checklist": [],
                    "editor_text": "",
                },
                "article": {
                    "working_title": "How small teams can publish consistently",
                    "article_goal": "educate_with_practical_guidance",
                    "target_audience": ["Small content teams"],
                    "notes": "Small teams need clear ownership and lightweight handoffs.",
                },
                "brief": {
                    "summary": "A practical guide to consistent publishing.",
                    "core_angle": "Consistency comes from clear ownership.",
                    "audience_insights": ["Small teams need a lightweight process"],
                    "tone_and_style": "Clear and pragmatic",
                    "key_takeaways": ["Assign ownership"],
                    "evidence_gaps": [],
                    "call_to_action": "Create a publishing checklist",
                },
                "other_sections": [],
            },
            "Keep the section concise.",
        )
    finally:
        await generator.close()

    assert result.draft.blocks
    assert result.model_id == settings.vertex_model_id
