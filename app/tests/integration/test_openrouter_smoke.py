import os

import pytest

from app.core.config import Settings
from app.services.ai_service import BriefSource, OutlineSource, TalkingPointsSource
from app.services.openrouter_ai import (
    OpenRouterBriefGenerator,
    OpenRouterJSONClient,
    OpenRouterOutlineGenerator,
    OpenRouterSectionInterviewGenerator,
    OpenRouterTalkingPointsGenerator,
)

pytestmark = pytest.mark.openrouter


@pytest.mark.skipif(
    os.getenv("RUN_OPENROUTER_SMOKE_TEST") != "true",
    reason="Set RUN_OPENROUTER_SMOKE_TEST=true to call OpenRouter",
)
async def test_openrouter_generates_all_structured_outputs(settings: Settings) -> None:
    if settings.openrouter_api_key is None:
        pytest.fail("OPENROUTER_API_KEY is required for the OpenRouter smoke test")
    client = OpenRouterJSONClient(settings)
    try:
        brief = await OpenRouterBriefGenerator(client).generate(
            BriefSource(
                working_title="How small teams can publish consistently",
                notes="Small teams struggle with ownership and irregular review cycles.",
                target_audience=["Independent writers", "Small content teams"],
                article_goal="educate_with_practical_guidance",
            )
        )
        outline = await OpenRouterOutlineGenerator(client).generate(
            OutlineSource(
                summary=brief.brief.summary,
                core_angle=brief.brief.core_angle,
                audience_insights=brief.brief.audience_insights,
                tone_and_style=brief.brief.tone_and_style,
                key_takeaways=brief.brief.key_takeaways,
                evidence_gaps=brief.brief.evidence_gaps,
                call_to_action=brief.brief.call_to_action,
            )
        )
        context: dict[str, object] = {
            "article": {
                "working_title": "How small teams can publish consistently",
                "article_goal": "educate_with_practical_guidance",
                "target_audience": ["Small content teams"],
            },
            "selected_section": {
                "id": "section-one",
                "title": outline.outline.sections[0].heading,
                "goal": outline.outline.sections[0].purpose,
                "outline_key_points": outline.outline.sections[0].key_points,
                "editor_text": "",
            },
            "other_sections": [],
        }
        points = await OpenRouterTalkingPointsGenerator(client).generate(
            TalkingPointsSource(context=context, instruction=None)
        )
        interview_generator = OpenRouterSectionInterviewGenerator(client)
        questions = await interview_generator.generate_questions(context, None)
        draft = await interview_generator.generate_draft(
            context,
            [
                {
                    **questions.questions.questions[0].model_dump(mode="json"),
                    "answer": "We assigned one named owner to every publishing stage.",
                }
            ],
        )
    finally:
        await client.close()

    assert brief.brief.core_angle
    assert outline.outline.sections
    assert points.talking_points.points
    assert 2 <= len(questions.questions.questions) <= 4
    assert draft.draft.blocks
