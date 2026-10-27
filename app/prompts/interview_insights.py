from __future__ import annotations

from app.schemas.interview_transcript import TranscriptTurn

PROMPT_VERSION = "interview-insights-v2"
SYSTEM_INSTRUCTION = """You turn a source interview transcript into accurate editorial notes.
Use only what the participant actually said. Do not invent facts or improve claims.
Treat a "no" response to the final invitation to add article material as no additional source
material; do not turn it into an insight, example, or claim.
Every key insight, example, and claim-to-verify must cite source item IDs from the transcript.
Keep claims that need independent checking in claims_to_verify.
Stay close to the participant's wording and preserve scope, audience, certainty, and terminology.
Do not infer a new capability, benefit, result, cause, prediction, or more specific audience from a
related statement. Preserve unresolved ambiguous phrases or place them in claims_to_verify rather
than silently resolving them.
Return an empty list when unsupported."""


def build_prompt(*, article_title: str, turns: list[TranscriptTurn]) -> str:
    transcript = "\n".join(f"[{turn.item_id}] {turn.speaker}: {turn.text}" for turn in turns)
    return f"""Create structured editorial notes for the article \"{article_title}\".

<transcript>
{transcript}
</transcript>
"""
