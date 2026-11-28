from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Literal

from app.prompts.consultant import (
    CONSULTANT_VOICE_DELIVERY_POLICY,
    CONSULTANT_VOICE_WORKFLOW_POLICY,
    source_json,
)

OpeningStyle = Literal["warm", "casual", "playful"]


def choose_opening_style() -> OpeningStyle:
    # Most starts are warm/casual; humour is an occasional variation.
    return secrets.choice(("warm", "warm", "casual", "casual", "playful"))


@dataclass(frozen=True)
class ConsultantVoiceContext:
    name: str
    title: str
    description: str
    audience: list[str]
    questions: list[dict[str, Any]]
    opening_style: OpeningStyle = field(default_factory=choose_opening_style)

    def _start_instructions(self, *, resumed: bool) -> str:
        if resumed:
            return """SESSION START: RESUME
This connection continues an interview whose interviewer has already spoken. Briefly acknowledge
the reconnection, then continue the last unfinished exchange from the prior conversation.
Do not repeat the introduction or questions already answered. The speech model must wait for
the backend-approved continuation rather than improvising a connection acknowledgment."""
        style = {
            "warm": "Use a warm greeting and an easy social check-in. Do not make a joke.",
            "casual": "Use a relaxed, conversational greeting and check-in. Do not make a joke.",
            "playful": (
                "You may use one short, gentle, situational joke with the greeting and check-in. "
                "Humour is optional; prefer warmth if it would feel forced."
            ),
        }[self.opening_style]
        return f"""SESSION START: INTRODUCTION REQUIRED
The conversation has not yet started. Follow the light-opening exchange in the interview policy.
Selected opening style: {self.opening_style}. {style}
First greet the consultant by name, identify yourself briefly as Inkwell, and ask one easy social
check-in in a single short turn. Then stop and wait for their reply. Do not introduce the article,
explain draft creation, or ask a substantive interview question in this first turn.
If they have already said hello or asked whether you can hear them, acknowledge it naturally and
invite a brief reply. Never open with only "OK, I hear you", "Got it", or "Let's continue".
If they have already started discussing their article or explicitly want to begin immediately,
follow their lead instead of forcing small talk.
The workflow backend supplies the greeting and social exchange as ordinary commentary; do not
call present_question for social check-ins. The speech model must wait for the approved greeting,
speak only that turn, then leave room for the consultant to answer. Do not improvise an extra
greeting, append the article introduction, or advance into interview questions independently."""

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
