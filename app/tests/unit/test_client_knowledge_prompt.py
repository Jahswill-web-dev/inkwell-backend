import json

from app.db.models.client_knowledge import KNOWLEDGE_CATEGORIES
from app.prompts.client_knowledge import (
    CATEGORY_GUIDANCE,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_prompt,
)
from app.schemas.interview_transcript import TranscriptTurn


def test_prompt_covers_the_taxonomy_and_evidence_rules() -> None:
    assert PROMPT_VERSION == "client-knowledge-v1"
    assert set(CATEGORY_GUIDANCE) == set(KNOWLEDGE_CATEGORIES)
    assert "participant transcript item IDs" in SYSTEM_INSTRUCTION
    assert "must never be cited" in SYSTEM_INSTRUCTION
    assert "use create as proposed_action" in SYSTEM_INSTRUCTION
    assert "Return an empty proposals list" in SYSTEM_INSTRUCTION


def test_prompt_serializes_client_context_and_transcript_as_json() -> None:
    prompt = build_prompt(
        client_name="Northstar Labs",
        client_website="https://northstar.example",
        client_industry="Software",
        article_title="How research teams work",
        interviewee_name="Avery Chen",
        turns=[
            TranscriptTurn(
                item_id="participant-1",
                speaker="participant",
                text='We call it "evidence mapping".',
                occurred_at_ms=500,
            )
        ],
    )

    source_json = prompt.split("Source JSON:\n", maxsplit=1)[1].strip()
    source = json.loads(source_json)
    assert source["client"] == {
        "name": "Northstar Labs",
        "website": "https://northstar.example",
        "industry": "Software",
    }
    assert source["interview"]["article_title"] == "How research teams work"
    assert source["interview"]["turns"][0] == {
        "item_id": "participant-1",
        "speaker": "participant",
        "text": 'We call it "evidence mapping".',
        "occurred_at_ms": 500,
    }
    for category in KNOWLEDGE_CATEGORIES:
        assert f"- {category}:" in prompt
