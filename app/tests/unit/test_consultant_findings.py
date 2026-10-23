import json
from uuid import UUID

from app.schemas.consultant import DraftFinding
from app.services.consultant_findings import (
    anchor_findings,
    ensure_editor_anchors,
    locate_paragraph,
    paragraph_text,
    replace_paragraph_text,
)


def sections() -> list[dict[str, object]]:
    return [{
        "id": "not-yet-stable",
        "title": "Uses of AI",
        "editor_state": json.dumps({
            "root": {
                "type": "root",
                "version": 1,
                "children": [{
                    "type": "paragraph",
                    "version": 1,
                    "children": [{"type": "text", "version": 1, "text": "AI recognises patterns."}],
                }],
            }
        }),
    }]


def test_anchors_and_deduplicates_exact_claim_findings() -> None:
    content = ensure_editor_anchors(sections())
    finding = DraftFinding(
        classification="unsupported",
        category="unsupported_prediction",
        section_index=0,
        paragraph_index=0,
        claim_text="makes predictions",
        explanation="Prediction is not in the cited turn.",
        source_turn_ids=["t1"],
    )
    anchored = anchor_findings([finding, finding], content)

    assert len(anchored) == 1
    assert isinstance(anchored[0].section_id, UUID)
    assert isinstance(anchored[0].paragraph_id, UUID)
    editor = json.loads(content[0]["editor_state"])
    assert editor["root"]["children"][0]["type"] == "anchored-paragraph"


def test_supported_findings_are_not_materialized() -> None:
    content = ensure_editor_anchors(sections())
    finding = DraftFinding(
        classification="supported",
        category="wording",
        section_index=0,
        paragraph_index=0,
        explanation="No warning is needed.",
    )

    assert anchor_findings([finding], content) == []


def test_focused_replace_changes_only_the_target_paragraph() -> None:
    content = ensure_editor_anchors(sections())
    editor = json.loads(content[0]["editor_state"])
    node = editor["root"]["children"][0]
    section, paragraph, _, _ = locate_paragraph(
        content, content[0]["id"], node["paragraphId"], 0, 0
    )
    replace_paragraph_text(paragraph, "AI analyses data and recognises patterns.")

    assert section["title"] == "Uses of AI"
    assert paragraph_text(paragraph) == "AI analyses data and recognises patterns."
