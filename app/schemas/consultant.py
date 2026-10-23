from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    paragraph_id: UUID | None = None
    text: str
    source_turn_ids: list[str]
    direct_quotes: list[str]
    external_source_ids: list[str] = Field(default_factory=list)


class GeneratedSection(BaseModel):
    section_id: UUID | None = None
    heading: str
    paragraphs: list[DraftParagraph] = Field(min_length=1)


class GeneratedDraft(BaseModel):
    title: str
    sections: list[GeneratedSection] = Field(min_length=1)
    gaps: list[str]
    summary: str = ""


class DraftFinding(BaseModel):
    id: UUID | None = None
    classification: Literal["supported", "interpretation", "unsupported"] | None = None
    severity: Literal["serious", "style"] | None = None
    category: Literal[
        "unsupported_capability",
        "unsupported_benefit",
        "unsupported_audience",
        "unsupported_causality",
        "unsupported_prediction",
        "unsupported_fact",
        "unsupported_public_fact",
        "invented_experience",
        "incorrect_quotation",
        "changed_meaning",
        "wording",
        "readability",
    ]
    section_index: int = Field(ge=0)
    section_id: UUID | None = None
    paragraph_index: int | None = Field(default=None, ge=0)
    paragraph_id: UUID | None = None
    claim_text: str = Field(default="", max_length=4000)
    explanation: str = Field(min_length=1, max_length=500)
    source_turn_ids: list[str] = Field(default_factory=list)
    external_source_ids: list[str] = Field(default_factory=list)
    suggested_replacement: str | None = Field(default=None, max_length=10000)
    status: Literal["open", "resolved"] = "open"
    resolution_reason: str | None = Field(default=None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def map_legacy_severity(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        mapped = dict(value)
        if not mapped.get("classification"):
            mapped["classification"] = (
                "interpretation"
                if mapped.get("severity") == "style"
                and mapped.get("category") in {"wording", "readability"}
                else "unsupported"
            )
        if not mapped.get("severity"):
            mapped["severity"] = (
                "style" if mapped["classification"] == "interpretation" else "serious"
            )
        return mapped

    @model_validator(mode="after")
    def accuracy_cannot_be_downgraded(self) -> DraftFinding:
        self.severity = "serious" if self.classification == "unsupported" else "style"
        return self


class DraftCheck(BaseModel):
    findings: list[DraftFinding]

    @property
    def serious_issues(self) -> list[str]:
        return [
            finding.explanation
            for finding in self.findings
            if finding.classification == "unsupported"
        ]

    @property
    def style_issues(self) -> list[str]:
        return [
            finding.explanation
            for finding in self.findings
            if finding.classification == "interpretation"
        ]


class FindingRepairInput(Input):
    replacement_paragraph: str | None = Field(default=None, min_length=1, max_length=30000)


class FindingRepairPreview(BaseModel):
    finding_id: UUID
    before: str
    after: str
    classification: Literal["supported", "interpretation", "unsupported"]
    findings: list[DraftFinding] = Field(default_factory=list)
    target_version: str


class FocusedParagraphRepair(BaseModel):
    replacement_paragraph: str = Field(min_length=1, max_length=30000)


class SectionRepair(BaseModel):
    section_index: int = Field(ge=0)
    section: GeneratedSection


class SectionRepairs(BaseModel):
    repairs: list[SectionRepair] = Field(min_length=1)


class ArticleEditAction(BaseModel):
    kind: Literal["text_edit", "addition", "section_move", "full_rewrite"]
    target_section_id: str | None = None
    destination_section_id: str | None = None
    position: Literal["before", "after"] | None = None
    instruction: str = Field(min_length=1, max_length=1200)


class ArticleEditPlan(BaseModel):
    actions: list[ArticleEditAction] = Field(default_factory=list)
    clarification_question: str | None = Field(default=None, max_length=300)


class ArticleSectionEdits(BaseModel):
    repairs: list[SectionRepair]
    summary: str = Field(default="", max_length=500)


class ArticleFlowCheck(BaseModel):
    findings: list[DraftFinding] = Field(default_factory=list)


class ArticleTransitionEdit(BaseModel):
    section_index: int = Field(ge=0)
    paragraph: DraftParagraph


class ArticleTransitionEdits(BaseModel):
    edits: list[ArticleTransitionEdit] = Field(default_factory=list)


def json_data(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


class ArticleDetailsInput(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
