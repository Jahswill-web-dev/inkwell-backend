from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.login_rate_limit import LoginRateLimit


class LoginRateLimitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(
        self, *, scope: str, key_hash: str, now: datetime
    ) -> LoginRateLimit | None:
        statement = select(LoginRateLimit).where(
            LoginRateLimit.scope == scope,
            LoginRateLimit.key_hash == key_hash,
            LoginRateLimit.expires_at > now,
        )
        result = await self.session.scalars(statement)
        return result.first()

    async def increment(
        self,
        *,
        scope: str,
        key_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        statement = insert(LoginRateLimit).values(
            scope=scope,
            key_hash=key_hash,
            failure_count=1,
            window_started_at=now,
            expires_at=expires_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[LoginRateLimit.scope, LoginRateLimit.key_hash],
            set_={
                "failure_count": case(
                    (LoginRateLimit.expires_at <= now, 1),
                    else_=LoginRateLimit.failure_count + 1,
                ),
                "window_started_at": case(
                    (LoginRateLimit.expires_at <= now, now),
                    else_=LoginRateLimit.window_started_at,
                ),
                "expires_at": case(
                    (LoginRateLimit.expires_at <= now, expires_at),
                    else_=LoginRateLimit.expires_at,
                ),
            },
        )
        await self.session.execute(statement)

    async def clear(self, *, scope: str, key_hash: str) -> None:
        await self.session.execute(
            delete(LoginRateLimit).where(
                LoginRateLimit.scope == scope,
                LoginRateLimit.key_hash == key_hash,
            )
        )

    async def delete_expired(self, *, now: datetime) -> None:
        await self.session.execute(delete(LoginRateLimit).where(LoginRateLimit.expires_at <= now))
