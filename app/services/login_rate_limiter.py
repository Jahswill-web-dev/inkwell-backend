from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.repositories.login_rate_limit import LoginRateLimitRepository

EMAIL_SCOPE = "email"
IP_SCOPE = "ip"


class LoginRateLimiter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def ensure_allowed(self, *, email: str, client_ip: str) -> None:
        now = datetime.now(UTC)
        keys = (
            (EMAIL_SCOPE, _hash_key(email), self.settings.login_rate_limit_email_failures),
            (IP_SCOPE, _hash_key(client_ip), self.settings.login_rate_limit_ip_failures),
        )
        blocked: list[LoginRateLimit] = []
        async with self.session_factory() as session:
            repository = LoginRateLimitRepository(session)
            for scope, key_hash, threshold in keys:
                counter = await repository.get_active(scope=scope, key_hash=key_hash, now=now)
                if counter is not None and counter.failure_count >= threshold:
                    blocked.append(counter)

        if blocked:
            retry_after = max(
                1, ceil(max((item.expires_at - now).total_seconds() for item in blocked))
            )
            raise AppError(
                status_code=429,
                code="too_many_login_attempts",
                message="Too many login attempts. Please try again later",
                headers={"Retry-After": str(retry_after)},
            )

    async def record_failure(self, *, email: str, client_ip: str) -> None:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.settings.login_rate_limit_window_seconds)
        async with self.session_factory.begin() as session:
            repository = LoginRateLimitRepository(session)
            await repository.delete_expired(now=now)
            await repository.increment(
                scope=EMAIL_SCOPE,
                key_hash=_hash_key(email),
                now=now,
                expires_at=expires_at,
            )
            await repository.increment(
                scope=IP_SCOPE,
                key_hash=_hash_key(client_ip),
                now=now,
                expires_at=expires_at,
            )

    async def clear_email(self, *, email: str) -> None:
        async with self.session_factory.begin() as session:
            repository = LoginRateLimitRepository(session)
            await repository.clear(scope=EMAIL_SCOPE, key_hash=_hash_key(email))


def _hash_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
