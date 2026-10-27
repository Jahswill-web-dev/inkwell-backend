from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.config import Settings
from app.db.models.refresh_session import RefreshSession
from app.services.refresh_session_service import _parse, _token, _valid_generation


def test_refresh_credential_rotates_without_extending_absolute_expiry(settings: Settings) -> None:
    now = datetime.now(UTC)
    expiry = now + timedelta(days=30)
    record = RefreshSession(
        id=uuid4(), user_id=uuid4(), nonce="a" * 64, generation=0, expires_at=expiry
    )
    original = _token(record, settings)
    parsed = _parse(original)
    assert parsed is not None
    assert _valid_generation(record, parsed[1], parsed[2], settings, now)

    record.previous_generation = 0
    record.previous_valid_until = now + timedelta(seconds=10)
    record.generation = 1
    rotated = _token(record, settings)
    assert rotated != original
    assert record.expires_at == expiry
    assert _valid_generation(record, parsed[1], parsed[2], settings, now)
    assert not _valid_generation(
        record, parsed[1], parsed[2], settings, now + timedelta(seconds=11)
    )


def test_refresh_credential_rejects_tampering(settings: Settings) -> None:
    record = RefreshSession(
        id=uuid4(),
        user_id=uuid4(),
        nonce="b" * 64,
        generation=0,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    parsed = _parse(_token(record, settings))
    assert parsed is not None
    assert not _valid_generation(record, parsed[1], "0" * 64, settings, datetime.now(UTC))
    assert _parse("not-a-refresh-token") is None
