from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.client_knowledge import (
    ClientKnowledgeExtractionRun,
    ClientKnowledgeProposal,
)
from app.db.models.interview_invitation import InterviewInvitation
from app.db.models.interview_transcript import InterviewTranscript
from app.db.repositories.client_knowledge import ClientKnowledgeRepository
from app.prompts.client_knowledge import PROMPT_VERSION
from app.schemas.client_knowledge import (
    ClientKnowledgeProposalCreate,
    ClientKnowledgeSourceCreate,
    GeneratedKnowledgeProposals,
)
from app.schemas.interview_transcript import TranscriptTurn

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientKnowledgeExtractionInput:
    client_name: str
    client_website: str | None
    client_industry: str | None
    article_title: str
    interviewee_name: str
    turns: list[TranscriptTurn]


@dataclass(frozen=True)
class ClientKnowledgeGenerationResult:
    proposals: GeneratedKnowledgeProposals
    model_id: str


class ClientKnowledgeGenerator(Protocol):
    async def generate(
        self, source: ClientKnowledgeExtractionInput
    ) -> ClientKnowledgeGenerationResult: ...


class ClientKnowledgeProviderTimeoutError(Exception):
    pass


class ClientKnowledgeProviderUnavailableError(Exception):
    pass


class ClientKnowledgeProviderResponseError(Exception):
    pass


@dataclass(frozen=True)
class ClientKnowledgeExtractionOutcome:
    run: ClientKnowledgeExtractionRun
    proposals: Sequence[ClientKnowledgeProposal]
    reused: bool


@dataclass(frozen=True)
class _InterviewContext:
    workspace_id: UUID
    client_id: UUID
    transcript_id: UUID
    article_id: UUID
    client_name: str
    client_website: str | None
    client_industry: str | None
    article_title: str
    interviewee_name: str
    progress_state: str
    turns: list[TranscriptTurn]


class ClientKnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        generator: ClientKnowledgeGenerator | None = None,
    ) -> None:
        self.session = session
        self.generator = generator
        self.knowledge = ClientKnowledgeRepository(session)

    async def extract_from_interview(
        self, *, client_id: UUID, interview_id: UUID
    ) -> ClientKnowledgeExtractionOutcome:
        """Extract reviewable knowledge proposals from one completed interview."""
        if self.generator is None:
            raise AppError(
                status_code=503,
                code="knowledge_extraction_unavailable",
                message="Client knowledge extraction is not configured",
            )

        context = await self._load_interview(interview_id)
        if context is None or context.client_id != client_id:
            raise AppError(
                status_code=404,
                code="knowledge_interview_not_found",
                message="The completed client interview was not found",
            )
        if context.progress_state != "completed":
            raise AppError(
                status_code=409,
                code="knowledge_interview_not_completed",
                message="The interview must be completed before knowledge is extracted",
            )

        participant_turns = {
            turn.item_id: turn for turn in context.turns if turn.speaker == "participant"
        }
        if not participant_turns:
            raise AppError(
                status_code=422,
                code="knowledge_transcript_empty",
                message="The interview has no participant responses to extract",
            )

        transcript_hash = _transcript_content_hash(context.turns)
        run, created = await self.knowledge.create_run_if_missing(
            workspace_id=context.workspace_id,
            client_id=context.client_id,
            transcript_id=context.transcript_id,
            prompt_version=PROMPT_VERSION,
            transcript_content_hash=transcript_hash,
            model_id=None,
        )
        if not created and run.status == "completed":
            return ClientKnowledgeExtractionOutcome(
                run=run,
                proposals=await self.knowledge.list_proposals_for_run(run.id),
                reused=True,
            )

        claimed_run = await self.knowledge.claim_run_for_processing(run.id)
        if claimed_run is None:
            current = await self.knowledge.get_run(run.id)
            if current is not None and current.status == "completed":
                return ClientKnowledgeExtractionOutcome(
                    run=current,
                    proposals=await self.knowledge.list_proposals_for_run(current.id),
                    reused=True,
                )
            raise AppError(
                status_code=409,
                code="knowledge_extraction_in_progress",
                message="Knowledge extraction is already in progress for this interview",
            )

        try:
            result = await self.generator.generate(
                ClientKnowledgeExtractionInput(
                    client_name=context.client_name,
                    client_website=context.client_website,
                    client_industry=context.client_industry,
                    article_title=context.article_title,
                    interviewee_name=context.interviewee_name,
                    turns=context.turns,
                )
            )
            drafts = _resolve_proposals(result.proposals, participant_turns)
        except Exception as exc:
            logger.exception(
                "client_knowledge_extraction_failed interview_id=%s run_id=%s",
                interview_id,
                claimed_run.id,
            )
            await self.knowledge.mark_run_failed(
                claimed_run,
                error_message=_safe_extraction_error(exc),
            )
            return ClientKnowledgeExtractionOutcome(
                run=claimed_run,
                proposals=(),
                reused=False,
            )

        claimed_run.model_id = result.model_id
        proposals = await self.knowledge.save_proposals(
            claimed_run,
            article_id=context.article_id,
            interviewee_name=context.interviewee_name,
            proposals=drafts,
        )

        logger.info(
            "client_knowledge_extraction_completed interview_id=%s run_id=%s proposal_count=%d",
            interview_id,
            claimed_run.id,
            len(proposals),
        )
        return ClientKnowledgeExtractionOutcome(
            run=claimed_run,
            proposals=proposals,
            reused=False,
        )

    async def _load_interview(self, interview_id: UUID) -> _InterviewContext | None:
        row = (
            await self.session.execute(
                select(InterviewInvitation, InterviewTranscript)
                .join(
                    InterviewTranscript,
                    InterviewTranscript.invitation_id == InterviewInvitation.id,
                )
                .options(selectinload(InterviewInvitation.article).selectinload(Article.client))
                .where(InterviewInvitation.id == interview_id)
            )
        ).one_or_none()
        if row is None:
            return None

        invitation, transcript = row
        article = invitation.article
        client = article.client
        if article.client_id is None or client is None:
            return None
        try:
            turns = [TranscriptTurn.model_validate(turn) for turn in transcript.turns]
        except ValidationError as exc:
            raise AppError(
                status_code=422,
                code="knowledge_transcript_invalid",
                message="The interview transcript is invalid",
            ) from exc

        return _InterviewContext(
            workspace_id=invitation.workspace_id,
            client_id=article.client_id,
            transcript_id=transcript.id,
            article_id=article.id,
            client_name=client.name,
            client_website=client.website,
            client_industry=client.industry,
            article_title=article.working_title,
            interviewee_name=invitation.participant_name,
            progress_state=invitation.progress_state,
            turns=turns,
        )


def _resolve_proposals(
    generated: GeneratedKnowledgeProposals,
    participant_turns: dict[str, TranscriptTurn],
) -> list[ClientKnowledgeProposalCreate]:
    resolved: list[ClientKnowledgeProposalCreate] = []
    for proposal in generated.proposals:
        if proposal.proposed_action != "create":
            raise AppError(
                status_code=502,
                code="knowledge_extraction_invalid",
                message="Knowledge extraction proposed an unsupported action",
            )
        unknown_ids = set(proposal.source_item_ids) - participant_turns.keys()
        if unknown_ids:
            raise AppError(
                status_code=502,
                code="knowledge_extraction_invalid",
                message="Knowledge extraction referenced invalid transcript sources",
            )
        resolved.append(
            ClientKnowledgeProposalCreate(
                category=proposal.category,
                statement=proposal.statement,
                knowledge_type=proposal.knowledge_type,
                proposed_action=proposal.proposed_action,
                confidence=proposal.confidence,
                needs_verification=proposal.needs_verification,
                valid_until=proposal.valid_until,
                sources=[
                    ClientKnowledgeSourceCreate(
                        transcript_item_id=item_id,
                        quoted_text=participant_turns[item_id].text,
                        occurred_at_ms=participant_turns[item_id].occurred_at_ms,
                    )
                    for item_id in proposal.source_item_ids
                ],
            )
        )
    return resolved


def _transcript_content_hash(turns: list[TranscriptTurn]) -> str:
    canonical = [
        {
            "item_id": turn.item_id,
            "speaker": turn.speaker,
            "text": turn.text,
            "occurred_at_ms": turn.occurred_at_ms,
        }
        for turn in turns
    ]
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_extraction_error(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message
    if isinstance(exc, ClientKnowledgeProviderTimeoutError):
        return "Client knowledge extraction timed out"
    if isinstance(exc, ClientKnowledgeProviderUnavailableError):
        return "Client knowledge extraction is temporarily unavailable"
    if isinstance(exc, ClientKnowledgeProviderResponseError):
        return "Client knowledge extraction returned an invalid response"
    return "Client knowledge could not be extracted"
