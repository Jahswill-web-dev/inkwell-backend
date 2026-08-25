"""Import domain models here so Alembic can discover their metadata."""

from app.db.models.article import Article
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_draft import ArticleDraft
from app.db.models.article_outline import ArticleOutline
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.section_interview import SectionInterview
from app.db.models.user import User

__all__ = [
    "Article",
    "ArticleBrief",
    "ArticleDraft",
    "ArticleOutline",
    "LoginRateLimit",
    "SectionInterview",
    "User",
]
