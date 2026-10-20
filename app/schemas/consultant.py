from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.interview_transcript import TranscriptTurn


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExperienceInput(Input):
    experience: Literal["marketing", "consultant"]


class ProfileInput(Input):
    display_name: str = Field(min_length=1, max_length=120)
    specialty: str = Field(default="", max_length=500)
    audience: str = Field(default="", max_length=500)


class InterviewVoiceEvidence(BaseModel):
    vocabulary: list[str] = Field(default_factory=list, max_length=30)
    explanation_patterns: str = Field(default="", max_length=2000)
    analogies: str = Field(default="", max_length=1000)
    formality: str = Field(default="", max_length=1000)
    rhythm: str = Field(default="", max_length=1000)
    perspective: str = Field(default="", max_length=1000)
    avoid: list[str] = Field(default_factory=list, max_length=30)
    excerpts: list[str] = Field(default_factory=list, max_length=20)


class ConsultantArticleInput(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
    audience: str = Field(default="", max_length=500)
    takeaway: str = Field(default="", max_length=500)
    length: Literal["short", "standard", "long"] = "standard"


class TurnsInput(Input):
    turns: list[TranscriptTurn] = Field(max_length=100)


class Correction(Input):
    text: str | None = Field(default=None, max_length=20000)
    excluded: bool = False


class CorrectionsInput(Input):
    corrections: dict[str, Correction]


class GenerateInput(Input):
    request_key: str = Field(min_length=1, max_length=120)
    instruction: Literal["initial", "clearer", "concise", "closer", "updated"] = "updated"


class ArticleAIRequest(Input):
    request_key: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=3, max_length=1200)


class TypedAnswer(Input):
    item_id: str = Field(min_length=1, max_length=190)
    text: str = Field(min_length=1, max_length=20000)
    skip: bool = False


class NextQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    complete: bool


class Claim(BaseModel):
    statement: str
    turn_ids: list[str]


class Evidence(BaseModel):
    argument: str
    claims: list[Claim]
    outline: list[str]
    gaps: list[str]
    voice: InterviewVoiceEvidence


class DraftParagraph(BaseModel):
    text: str
    source_turn_ids: list[str]
    direct_quotes: list[str]
    external_source_ids: list[str] = Field(default_factory=list)


class GeneratedSection(BaseModel):
    heading: str
    paragraphs: list[DraftParagraph] = Field(min_length=1)


class GeneratedDraft(BaseModel):
    title: str
    sections: list[GeneratedSection] = Field(min_length=1)
    gaps: list[str]
    summary: str = ""


class DraftCheck(BaseModel):
    serious_issues: list[str]
    style_issues: list[str]


def json_data(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


class ArticleDetailsInput(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
