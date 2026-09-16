from __future__ import annotations

from app.schemas.interview_transcript import TranscriptTurn

PROMPT_VERSION = "interview-insights-v1"
SYSTEM_INSTRUCTION = """You turn a source interview transcript into accurate editorial notes.
Use only what the participant actually said. Do not invent facts or improve claims.
Every key insight, example, and claim-to-verify must cite source item IDs from the transcript.
Keep claims that need independent checking in claims_to_verify.
Return an empty list when unsupported."""


def build_prompt(*, article_title: str, turns: list[TranscriptTurn]) -> str:
    transcript = "\n".join(f"[{turn.item_id}] {turn.speaker}: {turn.text}" for turn in turns)
    return f"""Create structured editorial notes for the article \"{article_title}\".

<transcript>
{transcript}
</transcript>
"""
