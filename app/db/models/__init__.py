"""Import domain models here so Alembic can discover their metadata."""

from app.db.models.article import Article
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_draft import ArticleDraft
from app.db.models.article_outline import ArticleOutline
from app.db.models.client import Client, ClientBrandProfile
from app.db.models.client_knowledge import (
    ClientKnowledgeExtractionRun,
    ClientKnowledgeProposal,
    ClientKnowledgeProposalSource,
    ClientKnowledgeSource,
)
from app.db.models.interview_invitation import InterviewInvitation
from app.db.models.interview_transcript import InterviewTranscript
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.section_interview import SectionInterview
from app.db.models.user import User
from app.db.models.voice_session import VoiceSession
from app.db.models.workspace import Workspace, WorkspaceMember

__all__ = [
    "Article",
    "ArticleBrief",
    "ArticleDraft",
    "ArticleOutline",
    "Client",
    "ClientBrandProfile",
    "ClientKnowledgeExtractionRun",
    "ClientKnowledgeProposal",
    "ClientKnowledgeProposalSource",
    "ClientKnowledgeSource",
    "InterviewInvitation",
    "InterviewTranscript",
    "LoginRateLimit",
    "SectionInterview",
    "User",
    "VoiceSession",
    "Workspace",
    "WorkspaceMember",
]
