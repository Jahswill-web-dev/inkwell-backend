from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.prompts.consultant import INTERVIEW_POLICY, source_json


@dataclass(frozen=True)
class ConsultantVoiceContext:
    name: str
    title: str
    description: str
    audience: list[str]
    questions: list[dict[str, Any]]

    def instructions(self, previous: str = "") -> str:
        return (
            INTERVIEW_POLICY
            + "\nSOURCE_CONTEXT:\n"
            + source_json(
                {
                    "name": self.name,
                    "title": self.title,
                    "description": self.description,
                    "audience": self.audience,
                    "coverage": self.questions,
                    "previous_conversation": previous,
                }
            )
        )
