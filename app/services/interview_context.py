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

    def _start_instructions(self, *, resumed: bool) -> str:
        if resumed:
            return """SESSION START: RESUME
This connection continues an interview whose interviewer has already spoken. Briefly acknowledge
the reconnection, then continue the last unfinished exchange from the prior conversation.
Do not repeat the introduction or questions already answered. The speech model must wait for
the backend-approved continuation rather than improvising a connection acknowledgment."""
        return """SESSION START: INTRODUCTION REQUIRED
The introduction has not yet been given. Your first response must greet the consultant by name,
identify yourself as Inkwell, orient them to the article and explain that their answers will be
used to create a draft. Explain that you will ask one question at a time. Then ask one easy,
article-specific opening question in that same turn, following the new-interview policy.
Do not skip this introduction because the consultant says hello, asks if you can hear them,
or because there is an incidental participant transcript. A microphone check is not an interview
answer or evidence that the interview has begun. Never open with a standalone acknowledgment
such as "OK, I hear you", "Got it", or "Let's continue".
The workflow backend must supply the complete introduction and opening question together in the
first present_question spoken_text. The speech model must wait for that approved response and
deliver its introduction before its question; it must not improvise an earlier greeting or reply."""

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

    def delivery_instructions(self, previous: str = "", *, resumed: bool | None = None) -> str:
        return (
            CONSULTANT_VOICE_DELIVERY_POLICY
            + "\n\n"
            + self._start_instructions(resumed=bool(previous) if resumed is None else resumed)
            + "\nSOURCE_CONTEXT:\n"
            + self._source_context(previous)
        )

    def workflow_instructions(self, previous: str = "", *, resumed: bool | None = None) -> str:
        return (
            CONSULTANT_VOICE_WORKFLOW_POLICY
            + "\n\n"
            + self._start_instructions(resumed=bool(previous) if resumed is None else resumed)
            + "\nSOURCE_CONTEXT:\n"
            + self._source_context(previous)
        )
