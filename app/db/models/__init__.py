"""Import domain models here so Alembic can discover their metadata."""

from app.db.models.article import Article
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.user import User

__all__ = ["Article", "LoginRateLimit", "User"]
