from typing import cast

from sqlalchemy import CheckConstraint, Table, UniqueConstraint

from app.db.models.client_knowledge import (
    KNOWLEDGE_CATEGORIES,
    ClientKnowledgeExtractionRun,
    ClientKnowledgeProposal,
    ClientKnowledgeProposalSource,
    ClientKnowledgeSource,
)


def test_knowledge_categories_match_the_product_taxonomy() -> None:
    assert KNOWLEDGE_CATEGORIES == (
        "company",
        "products_and_services",
        "audience_and_customers",
        "problems_and_needs",
        "positioning_and_differentiation",
        "messaging_and_terminology",
        "proof_points_and_results",
        "stories_and_examples",
        "expert_perspectives",
        "processes_and_frameworks",
    )


def test_proposal_pipeline_registers_all_four_tables() -> None:
    assert cast(Table, ClientKnowledgeExtractionRun.__table__).name == (
        "client_knowledge_extraction_runs"
    )
    assert cast(Table, ClientKnowledgeProposal.__table__).name == "client_knowledge_proposals"
    assert cast(Table, ClientKnowledgeSource.__table__).name == "client_knowledge_sources"
    assert cast(Table, ClientKnowledgeProposalSource.__table__).name == (
        "client_knowledge_proposal_sources"
    )


def test_extraction_run_is_idempotent_for_the_same_transcript_version() -> None:
    table = cast(Table, ClientKnowledgeExtractionRun.__table__)
    unique_constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "uq_knowledge_extraction_runs_transcript_version_hash" in unique_constraints


def test_proposal_has_database_guards_for_ai_generated_values() -> None:
    table = cast(Table, ClientKnowledgeProposal.__table__)
    check_constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_constraints == {
        "ck_knowledge_proposals_action",
        "ck_knowledge_proposals_category",
        "ck_knowledge_proposals_confidence",
        "ck_knowledge_proposals_reviewed_statement_not_blank",
        "ck_knowledge_proposals_reviewed_statement_status",
        "ck_knowledge_proposals_review_status",
        "ck_knowledge_proposals_statement_not_blank",
        "ck_knowledge_proposals_type",
    }


def test_interview_sources_require_a_transcript_and_turn_reference() -> None:
    table = cast(Table, ClientKnowledgeSource.__table__)
    check_constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_knowledge_sources_interview_reference" in check_constraints
    assert "ck_knowledge_sources_quoted_text_not_blank" in check_constraints


def test_proposal_source_link_uses_a_composite_primary_key() -> None:
    table = cast(Table, ClientKnowledgeProposalSource.__table__)
    primary_key_columns = set(table.primary_key.columns.keys())

    assert primary_key_columns == {"proposal_id", "source_id"}
