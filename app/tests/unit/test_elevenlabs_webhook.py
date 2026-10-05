from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.api.v1.elevenlabs_webhooks import _provider_metadata, _verify_signature
from app.core.config import Settings
from app.core.exceptions import AppError


def settings(secret: str | None = "webhook-secret-at-least-sixteen") -> Settings:
    return Settings(
        database_url="postgresql+psycopg://user:pass@localhost/database",
        jwt_secret_key="a-secure-test-secret-with-32-characters",
        elevenlabs_webhook_secret=secret,
        _env_file=None,
    )


def test_accepts_valid_elevenlabs_signature() -> None:
    body = b'{"type":"post_call_transcription"}'
    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"webhook-secret-at-least-sixteen",
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()

    _verify_signature(body, f"t={timestamp},v0={digest}", settings())


@pytest.mark.parametrize("signature", [None, "t=1,v0=invalid", "bad-header"])
def test_rejects_invalid_or_expired_elevenlabs_signature(signature: str | None) -> None:
    with pytest.raises(AppError) as error:
        _verify_signature(b"{}", signature, settings())

    assert error.value.code == "invalid_webhook_signature"


def test_sanitizes_provider_metadata() -> None:
    value = _provider_metadata(
        {
            "metadata": {"call_duration_secs": 12, "secret": "discard"},
            "analysis": {"transcript_summary": "Summary", "other": "discard"},
        }
    )

    assert value["call_duration_secs"] == 12
    assert value["transcript_summary"] == "Summary"
    assert "secret" not in value
