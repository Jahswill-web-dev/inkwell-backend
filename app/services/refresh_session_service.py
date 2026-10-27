from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.models.refresh_session import RefreshSession
from app.db.models.user import User

ROTATION_GRACE_SECONDS = 10


@dataclass(frozen=True, slots=True)
class RefreshCredentials:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


def _token(record: RefreshSession, settings: Settings) -> str:
    return _token_for_generation(record, record.generation, settings)


def _parse(value: str) -> tuple[UUID, int, str] | None:
    try:
        raw_id, raw_generation, signature = value.split(".")
        if len(signature) != 64 or not all(char in "0123456789abcdef" for char in signature):
            return None
        generation = int(raw_generation)
        if generation < 0:
            return None
        return UUID(raw_id), generation, signature
    except (ValueError, TypeError):
        return None


def _valid_generation(
    record: RefreshSession, generation: int, signature: str, settings: Settings, now: datetime
) -> bool:
    expected = _token_for_generation(record, generation, settings).rsplit(".", 1)[-1]
    if not hmac.compare_digest(signature, expected):
        return False
    return generation == record.generation or (
        generation == record.previous_generation
        and record.previous_valid_until is not None
        and now <= record.previous_valid_until
    )


def _token_for_generation(record: RefreshSession, generation: int, settings: Settings) -> str:
    message = f"{record.id}:{generation}:{record.nonce}".encode()
    signature = hmac.new(settings.jwt_secret_key.encode(), message, hashlib.sha256).hexdigest()
    return f"{record.id}.{generation}.{signature}"


def _invalid_session() -> AppError:
    return AppError(
        status_code=401, code="invalid_refresh_session", message="Please sign in again."
    )


async def issue_refresh_session(
    session: AsyncSession, user: User, settings: Settings
) -> RefreshCredentials:
    now = datetime.now(UTC)
    await session.execute(
        delete(RefreshSession).where(RefreshSession.expires_at < now - timedelta(days=7))
    )
    record = RefreshSession(
        user_id=user.id,
        nonce=secrets.token_hex(32),
        generation=0,
        expires_at=now + timedelta(days=settings.refresh_session_days),
    )
    session.add(record)
    await session.flush()
    return RefreshCredentials(
        access_token=create_access_token(str(user.id), settings),
        refresh_token=_token(record, settings),
        refresh_expires_at=record.expires_at,
    )


async def rotate_refresh_session(
    session: AsyncSession, value: str, settings: Settings
) -> RefreshCredentials:
    parsed = _parse(value)
    if parsed is None:
        raise _invalid_session()
    session_id, generation, signature = parsed
    record = await session.scalar(
        select(RefreshSession).where(RefreshSession.id == session_id).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= now
        or not _valid_generation(record, generation, signature, settings, now)
    ):
        raise _invalid_session()
    user = await session.get(User, record.user_id)
    if user is None:
        raise _invalid_session()
    if generation == record.generation:
        record.previous_generation = generation
        record.previous_valid_until = now + timedelta(seconds=ROTATION_GRACE_SECONDS)
        record.generation += 1
        await session.flush()
    return RefreshCredentials(
        access_token=create_access_token(str(user.id), settings),
        refresh_token=_token(record, settings),
        refresh_expires_at=record.expires_at,
    )


async def revoke_refresh_session(session: AsyncSession, value: str, settings: Settings) -> None:
    parsed = _parse(value)
    if parsed is None:
        return
    session_id, generation, signature = parsed
    record = await session.scalar(
        select(RefreshSession).where(RefreshSession.id == session_id).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        record
        and record.revoked_at is None
        and _valid_generation(record, generation, signature, settings, now)
    ):
        record.revoked_at = now
        await session.flush()
