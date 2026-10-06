from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.client_knowledge import (
    ClientKnowledgeExtractionRun,
    ClientKnowledgeProposal,
    ClientKnowledgeProposalSource,
    ClientKnowledgeSource,
)
from app.schemas.client_knowledge import ClientKnowledgeProposalCreate


class ClientKnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_run(self, run_id: UUID) -> ClientKnowledgeExtractionRun | None:
        return await self.session.get(ClientKnowledgeExtractionRun, run_id)

    async def get_run_by_fingerprint(
        self,
        *,
        transcript_id: UUID,
        prompt_version: str,
        transcript_content_hash: str,
    ) -> ClientKnowledgeExtractionRun | None:
        result = await self.session.scalars(
            select(ClientKnowledgeExtractionRun).where(
                ClientKnowledgeExtractionRun.transcript_id == transcript_id,
                ClientKnowledgeExtractionRun.prompt_version == prompt_version,
                ClientKnowledgeExtractionRun.transcript_content_hash == transcript_content_hash,
            )
        )
        return result.first()

    async def create_run_if_missing(
        self,
        *,
        workspace_id: UUID,
        client_id: UUID,
        transcript_id: UUID,
        prompt_version: str,
        transcript_content_hash: str,
        model_id: str | None,
    ) -> tuple[ClientKnowledgeExtractionRun, bool]:
        statement = (
            insert(ClientKnowledgeExtractionRun)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                transcript_id=transcript_id,
                status="pending",
                prompt_version=prompt_version,
                model_id=model_id,
                transcript_content_hash=transcript_content_hash,
            )
            .on_conflict_do_nothing(
                constraint="uq_knowledge_extraction_runs_transcript_version_hash"
            )
            .returning(ClientKnowledgeExtractionRun)
        )
        run = await self.session.scalar(statement)
        if run is not None:
            return run, True

        existing = await self.get_run_by_fingerprint(
            transcript_id=transcript_id,
            prompt_version=prompt_version,
            transcript_content_hash=transcript_content_hash,
        )
        assert existing is not None
        return existing, False

    async def mark_run_processing(
        self, run: ClientKnowledgeExtractionRun
    ) -> ClientKnowledgeExtractionRun:
        run.status = "processing"
        run.started_at = run.started_at or datetime.now(UTC)
        run.completed_at = None
        run.error_message = None
        await self.session.flush()
        return run

    async def claim_run_for_processing(self, run_id: UUID) -> ClientKnowledgeExtractionRun | None:
        """Atomically claim a pending or failed run for one service invocation."""
        now = datetime.now(UTC)
        return await self.session.scalar(
            update(ClientKnowledgeExtractionRun)
            .where(
                ClientKnowledgeExtractionRun.id == run_id,
                ClientKnowledgeExtractionRun.status.in_(("pending", "failed")),
            )
            .values(
                status="processing",
                started_at=now,
                completed_at=None,
                error_message=None,
            )
            .returning(ClientKnowledgeExtractionRun)
        )

    async def mark_run_failed(
        self, run: ClientKnowledgeExtractionRun, *, error_message: str
    ) -> ClientKnowledgeExtractionRun:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.error_message = error_message[:500]
        await self.session.flush()
        return run

    async def save_proposals(
        self,
        run: ClientKnowledgeExtractionRun,
        *,
        article_id: UUID,
        interviewee_name: str | None,
        proposals: Sequence[ClientKnowledgeProposalCreate],
    ) -> Sequence[ClientKnowledgeProposal]:
        locked_run = await self.session.scalar(
            select(ClientKnowledgeExtractionRun)
            .where(ClientKnowledgeExtractionRun.id == run.id)
            .with_for_update()
        )
        assert locked_run is not None
        run = locked_run
        existing = await self.list_proposals_for_run(run.id)
        if existing:
            return existing

        created: list[ClientKnowledgeProposal] = []
        for draft in proposals:
            proposal = ClientKnowledgeProposal(
                id=uuid4(),
                extraction_run_id=run.id,
                workspace_id=run.workspace_id,
                client_id=run.client_id,
                category=draft.category,
                statement=draft.statement,
                knowledge_type=draft.knowledge_type,
                proposed_action=draft.proposed_action,
                confidence=draft.confidence,
                needs_verification=draft.needs_verification,
                valid_until=draft.valid_until,
            )
            self.session.add(proposal)
            await self.session.flush()

            for evidence in draft.sources:
                source = await self._get_or_create_interview_source(
                    run=run,
                    article_id=article_id,
                    interviewee_name=interviewee_name,
                    transcript_item_id=evidence.transcript_item_id,
                    quoted_text=evidence.quoted_text,
                    occurred_at_ms=evidence.occurred_at_ms,
                )
                await self.session.execute(
                    insert(ClientKnowledgeProposalSource)
                    .values(
                        proposal_id=proposal.id,
                        source_id=source.id,
                        relationship=evidence.relationship,
                    )
                    .on_conflict_do_nothing()
                )
            created.append(proposal)

        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.error_message = None
        await self.session.flush()
        for proposal in created:
            await self.session.refresh(proposal)
        return created

    async def list_proposals_for_run(self, run_id: UUID) -> Sequence[ClientKnowledgeProposal]:
        result = await self.session.scalars(
            select(ClientKnowledgeProposal)
            .where(ClientKnowledgeProposal.extraction_run_id == run_id)
            .order_by(ClientKnowledgeProposal.created_at, ClientKnowledgeProposal.id)
        )
        return result.all()

    async def get_proposal_for_review(
        self, *, proposal_id: UUID, workspace_id: UUID
    ) -> ClientKnowledgeProposal | None:
        result = await self.session.scalars(
            select(ClientKnowledgeProposal)
            .where(
                ClientKnowledgeProposal.id == proposal_id,
                ClientKnowledgeProposal.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        return result.first()

    async def record_proposal_review(
        self,
        proposal: ClientKnowledgeProposal,
        *,
        review_status: str,
        reviewed_statement: str | None,
        review_notes: str | None,
        reviewed_by_user_id: UUID,
    ) -> ClientKnowledgeProposal:
        proposal.review_status = review_status
        proposal.reviewed_statement = reviewed_statement
        proposal.review_notes = review_notes
        proposal.reviewed_by_user_id = reviewed_by_user_id
        proposal.reviewed_at = datetime.now(UTC)
        await self.session.flush()
        await self.session.refresh(proposal)
        return proposal

    async def list_sources_for_proposal(self, proposal_id: UUID) -> Sequence[ClientKnowledgeSource]:
        result = await self.session.scalars(
            select(ClientKnowledgeSource)
            .join(
                ClientKnowledgeProposalSource,
                ClientKnowledgeProposalSource.source_id == ClientKnowledgeSource.id,
            )
            .where(ClientKnowledgeProposalSource.proposal_id == proposal_id)
            .order_by(ClientKnowledgeSource.occurred_at_ms, ClientKnowledgeSource.id)
        )
        return result.all()

    async def _get_or_create_interview_source(
        self,
        *,
        run: ClientKnowledgeExtractionRun,
        article_id: UUID,
        interviewee_name: str | None,
        transcript_item_id: str,
        quoted_text: str,
        occurred_at_ms: int | None,
    ) -> ClientKnowledgeSource:
        statement = (
            insert(ClientKnowledgeSource)
            .values(
                id=uuid4(),
                workspace_id=run.workspace_id,
                client_id=run.client_id,
                source_type="interview_turn",
                transcript_id=run.transcript_id,
                transcript_item_id=transcript_item_id,
                article_id=article_id,
                interviewee_name=interviewee_name,
                quoted_text=quoted_text,
                occurred_at_ms=occurred_at_ms,
            )
            .on_conflict_do_nothing(constraint="uq_knowledge_sources_transcript_item")
            .returning(ClientKnowledgeSource)
        )
        source = await self.session.scalar(statement)
        if source is not None:
            return source

        existing = await self.session.scalar(
            select(ClientKnowledgeSource).where(
                ClientKnowledgeSource.transcript_id == run.transcript_id,
                ClientKnowledgeSource.transcript_item_id == transcript_item_id,
            )
        )
        assert existing is not None
        return existing
