"""Rate-limit recovery without real provider requests or sleeps."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from openai import RateLimitError

from app.schemas.consultant import DraftCheck
from app.services import consultant_writer as writer_module
from app.services.consultant_writer import ConsultantWriter, rate_limit_retry_seconds
from app.workers import consultant as worker


def limit_error(code: str = "rate_limit_exceeded", **headers: str) -> RateLimitError:
    return RateLimitError(
        "private transcript must not be logged",
        response=httpx.Response(
            429,
            headers={"x-request-id": "req-limit", **headers},
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        ),
        body={
            "code": code,
            "type": "insufficient_quota" if code == "insufficient_quota" else "rate_limit_error",
        },
    )


@pytest.mark.parametrize("operation", ["parse", "research"])
@pytest.mark.parametrize("quota", [False, True])
async def test_writer_recovers_or_stops_for_quota(
    monkeypatch: Any,
    caplog: Any,
    operation: str,
    quota: bool,
) -> None:
    error = limit_error(
        "insufficient_quota" if quota else "rate_limit_exceeded", **{"retry-after": "90"}
    )
    result = SimpleNamespace(
        output_parsed=DraftCheck(findings=[]), usage=None, output=[], output_text="Evidence"
    )
    call = AsyncMock(side_effect=[error, result])
    writer = object.__new__(ConsultantWriter)
    writer.client = SimpleNamespace(responses=SimpleNamespace(parse=call, create=call))
    writer.model = "test-model"
    writer.metrics = None
    writer.usage = {"input_tokens": 0, "output_tokens": 0}
    sleep = AsyncMock()
    monkeypatch.setattr(writer_module.asyncio, "sleep", sleep)

    async def invoke() -> None:
        if operation == "parse":
            await writer.parse(DraftCheck, "Check", {})
        else:
            await writer.research({})

    if quota:
        with pytest.raises(RateLimitError):
            await invoke()
        assert call.await_count == 1
        sleep.assert_not_awaited()
    else:
        await invoke()
        assert call.await_count == 2
        sleep.assert_awaited_once_with(90)
    assert "req-limit" in caplog.text
    assert "test-model" in caplog.text
    assert "private transcript" not in caplog.text


@pytest.mark.parametrize("operation", ["parse", "research"])
async def test_writer_stops_after_three_throttled_calls(monkeypatch: Any, operation: str) -> None:
    call = AsyncMock(side_effect=limit_error(**{"retry-after": "70"}))
    writer = object.__new__(ConsultantWriter)
    writer.client = SimpleNamespace(responses=SimpleNamespace(parse=call, create=call))
    writer.model, writer.metrics = "test-model", None
    writer.usage = {"input_tokens": 0, "output_tokens": 0}
    sleep = AsyncMock()
    monkeypatch.setattr(writer_module.asyncio, "sleep", sleep)
    with pytest.raises(RateLimitError):
        if operation == "parse":
            await writer.parse(DraftCheck, "Check", {})
        else:
            await writer.research({})
    assert call.await_count == 3
    assert sleep.await_count == 2


def test_retry_headers_and_fallback(monkeypatch: Any) -> None:
    assert rate_limit_retry_seconds(limit_error(**{"retry-after-ms": "2500"})) == 2.5
    future = datetime.now(UTC) + timedelta(seconds=120)
    assert (
        118
        < rate_limit_retry_seconds(limit_error(**{"retry-after": format_datetime(future)}))
        <= 120
    )
    monkeypatch.setattr(writer_module.random, "uniform", lambda *_: 0)
    for invalid in ("garbage", "nan", "inf", "-10"):
        error = limit_error(**{"retry-after": invalid})
        assert rate_limit_retry_seconds(error) == 65
        assert rate_limit_retry_seconds(error, 1) == 130


@pytest.mark.parametrize("code", ["rate_limit_exceeded", "insufficient_quota"])
@pytest.mark.parametrize("attempts", [1, 3])
async def test_worker_quota_is_terminal_and_throttling_respects_delay(
    monkeypatch: Any,
    code: str,
    attempts: int,
) -> None:
    job = SimpleNamespace(
        id=uuid4(),
        article_id=uuid4(),
        snapshot={},
        model_id="test-model",
        usage={},
        candidate=None,
        created_at=datetime.now(UTC),
        attempts=attempts,
        state="running",
        lease_token="token",
    )
    db = AsyncMock()
    db.get.return_value = db.scalar.return_value = job
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=db)
    context.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=context)
    factory.begin.return_value = context
    error = limit_error(code, **{"retry-after": "90"})
    writer = SimpleNamespace(
        usage={},
        write=AsyncMock(side_effect=error),
        client=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(worker, "ConsultantWriter", lambda *_: writer)
    monkeypatch.setattr(worker, "heartbeat", AsyncMock())
    monkeypatch.setattr(worker, "checkpoint", AsyncMock())
    monkeypatch.setattr(worker, "close_open_events", AsyncMock())
    monkeypatch.setattr(worker, "open_event", AsyncMock())
    started = datetime.now(UTC)

    await worker.process(factory, job.id, "token")

    if code == "insufficient_quota" or attempts == 3:
        assert job.state == "failed"
        assert job.lease_until is None
    else:
        assert job.state == "queued"
        assert job.lease_until >= started + timedelta(seconds=90)
    assert ("quota" in job.error) == (code == "insufficient_quota")
    assert job.lease_token is None
    writer.client.close.assert_awaited_once()
