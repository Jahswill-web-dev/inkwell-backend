from __future__ import annotations

import json
import re
from typing import Any, TypeVar
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import Settings
from app.prompts.consultant import WRITING_POLICY, source_json
from app.schemas.consultant import DraftCheck, Evidence, GeneratedDraft

T = TypeVar("T", bound=BaseModel)


class ConsultantWriter:
    def __init__(self, settings: Settings, model: str):
        if settings.openai_api_key is None:
            raise RuntimeError("Consultant writing requires OPENAI_API_KEY")
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.consultant_request_timeout_seconds,
            max_retries=0,
        )
        self.model = model
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    async def parse(self, schema: type[T], task: str, source: dict[str, Any]) -> T:
        result = await self.client.responses.parse(
            model=self.model,
            instructions=WRITING_POLICY + "\n" + task,
            input=source_json(source),
            text_format=schema,
            max_output_tokens=16000,
            store=False,
        )
        if result.usage:
            self.usage["input_tokens"] += result.usage.input_tokens
            self.usage["output_tokens"] += result.usage.output_tokens
        if result.output_parsed is None:
            raise ValueError("The model did not return complete structured content")
        return result.output_parsed

    async def understand(self, snapshot: dict[str, Any]) -> Evidence:
        return await self.parse(
            Evidence,
            "Extract a supported argument, claims with participant turn IDs, useful "
            "outline, gaps, and voice evidence. Voice excerpts must be verbatim "
            "participant text. Do not invent style evidence.",
            snapshot,
        )

    async def write(self, snapshot: dict[str, Any], evidence: dict[str, Any]) -> GeneratedDraft:
        return await self.parse(
            GeneratedDraft,
            "Write the complete blog post. The rewrite instruction applies to the current "
            "draft in base_sections, but factual evidence remains the participant "
            "transcript. initial/updated means create a coherent draft; clearer improves "
            "explanations; concise removes unnecessary words; closer preserves more of "
            "the interview wording.",
            {**snapshot, "evidence": evidence},
        )

    async def check(self, snapshot: dict[str, Any], draft: GeneratedDraft) -> DraftCheck:
        return await self.parse(
            DraftCheck,
            "Audit the draft against the actual participant transcript. Flag unsupported "
            "claims, invented examples, fabricated quotations, excluded material, changed "
            "certainty, old-profile fact leakage, and interviewer attribution as "
            "serious_issues. Flag generic wording, lost distinctive phrasing, repetitive "
            "paragraphs or poor readability as style_issues. Empty lists mean no issues.",
            {**snapshot, "candidate": draft.model_dump()},
        )


def validate_evidence(evidence: Evidence, turns: list[dict[str, Any]]) -> None:
    sources = {t["item_id"]: t["text"] for t in turns if t["speaker"] == "participant"}
    for claim in evidence.claims:
        if not claim.turn_ids or not set(claim.turn_ids).issubset(sources):
            raise ValueError("Evidence references an unknown participant turn")
    for excerpt in evidence.voice.excerpts:
        if not any(excerpt in text for text in sources.values()):
            raise ValueError("Voice excerpt is not present in participant speech")


def validate_draft(draft: GeneratedDraft, turns: list[dict[str, Any]]) -> list[str]:
    sources = {t["item_id"]: t["text"] for t in turns if t["speaker"] == "participant"}
    errors, paragraphs = [], set()
    for section in draft.sections:
        for paragraph in section.paragraphs:
            if not paragraph.source_turn_ids or not set(paragraph.source_turn_ids).issubset(
                sources
            ):
                errors.append("Paragraph references missing participant evidence")
                continue
            support = "\n".join(sources[x] for x in paragraph.source_turn_ids)
            for quote in paragraph.direct_quotes:
                if quote not in support or quote not in paragraph.text:
                    errors.append("Quotation does not match the cited participant text")
            for quoted in re.findall(r'[“"]([^”"\n]+)[”"]', paragraph.text):
                if quoted not in support:
                    errors.append("Quotation is not verbatim source wording")
            for number in re.findall(r"\b\d+(?:[.,]\d+)*%?", paragraph.text):
                if number not in support:
                    errors.append("Numerical claim is missing from cited evidence")
            normalized = " ".join(paragraph.text.lower().split())
            if not normalized or normalized in paragraphs:
                errors.append("Empty or repeated paragraph")
            paragraphs.add(normalized)
    return list(dict.fromkeys(errors))


def editor_sections(draft: GeneratedDraft) -> list[dict[str, Any]]:
    def paragraph(text: str) -> dict[str, Any]:
        return {
            "type": "paragraph",
            "version": 1,
            "direction": "ltr",
            "format": "",
            "indent": 0,
            "children": [
                {
                    "type": "text",
                    "version": 1,
                    "text": text,
                    "format": 0,
                    "detail": 0,
                    "mode": "normal",
                    "style": "",
                }
            ],
            "textFormat": 0,
            "textStyle": "",
        }

    return [
        {
            "id": str(uuid4()),
            "outline_section_id": None,
            "title": section.heading,
            "goal": "Explain the consultant's perspective",
            "checklist": [],
            "editor_state": json.dumps(
                {
                    "root": {
                        "type": "root",
                        "version": 1,
                        "direction": "ltr",
                        "format": "",
                        "indent": 0,
                        "children": [paragraph(p.text) for p in section.paragraphs],
                    }
                }
            ),
        }
        for section in draft.sections
    ]
