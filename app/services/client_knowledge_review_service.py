from __future__ import annotations

from uuid import UUID

from app.core.exceptions import AppError
from app.db.models.client_knowledge import ClientKnowledgeProposal
from app.db.repositories.client_knowledge import ClientKnowledgeRepository
from app.db.repositories.workspace import WorkspaceRepository
from app.schemas.client_knowledge import ClientKnowledgeProposalReview


class ClientKnowledgeReviewService:
    def __init__(
        self,
        knowledge: ClientKnowledgeRepository,
        workspaces: WorkspaceRepository,
    ) -> None:
        self.knowledge = knowledge
        self.workspaces = workspaces

    async def review_proposal(
        self,
        *,
        proposal_id: UUID,
        reviewer_user_id: UUID,
        payload: ClientKnowledgeProposalReview,
    ) -> ClientKnowledgeProposal:
        workspace = await self.workspaces.get_default_for_user(reviewer_user_id)
        if workspace is None:
            raise AppError(
                status_code=404,
                code="workspace_not_found",
                message="The default workspace was not found",
            )

        proposal = await self.knowledge.get_proposal_for_review(
            proposal_id=proposal_id,
            workspace_id=workspace[0].id,
        )
        if proposal is None:
            raise AppError(
                status_code=404,
                code="knowledge_proposal_not_found",
                message="The knowledge proposal was not found",
            )
        if proposal.review_status != "pending_review":
            raise AppError(
                status_code=409,
                code="knowledge_proposal_already_reviewed",
                message="The knowledge proposal has already been reviewed",
            )

        approved = payload.decision in {"approved", "edited_and_approved"}
        return await self.knowledge.record_proposal_review(
            proposal,
            review_status="approved" if approved else "rejected",
            reviewed_statement=payload.edited_statement,
            review_notes=payload.review_notes,
            reviewed_by_user_id=reviewer_user_id,
        )
