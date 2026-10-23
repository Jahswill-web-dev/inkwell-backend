from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.prompts.consultant import (
    CONSULTANT_VOICE_DELIVERY_POLICY,
    CONSULTANT_VOICE_WORKFLOW_POLICY,
    source_json,
)


@dataclass(frozen=True)
class ConsultantVoiceContext:
    name: str
    title: str
    description: str
    audience: list[str]
    questions: list[dict[str, Any]]

    def _source_context(self, previous: str = "") -> str:
        return source_json(
            {
                "consultant_name": self.name,
                "article_title": self.title,
                "article_description": self.description,
                "intended_audience": self.audience,
                "coverage_objectives": self.questions,
                "prior_conversation": previous,
            }
        )

    def delivery_instructions(self, previous: str = "") -> str:
        return (
            CONSULTANT_VOICE_DELIVERY_POLICY
            + "\nSOURCE_CONTEXT:\n"
            + self._source_context(previous)
        )

    def workflow_instructions(self, previous: str = "") -> str:
        return (
            CONSULTANT_VOICE_WORKFLOW_POLICY
            + "\nSOURCE_CONTEXT:\n"
            + self._source_context(previous)
        )
