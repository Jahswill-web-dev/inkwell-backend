"""Domain-specific database repositories."""

from app.db.repositories.login_rate_limit import LoginRateLimitRepository
from app.db.repositories.user import UserRepository

__all__ = ["LoginRateLimitRepository", "UserRepository"]
