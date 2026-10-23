from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import ConsultantGeneration, ConsultantGenerationEvent
from app.db.session import create_engine, create_session_factory
from app.services.consultant_metrics import MetricsRecorder
from app.tests.integration.test_consultant import (
    consultant_client as _consultant_client,
)
from app.tests.integration.test_consultant import (
    register,
    setup_article,
)
from app.workers import consultant as worker
from app.workers.consultant import claim

pytestmark = pytest.mark.database
consultant_client = _consultant_client


def queue_job(client: TestClient, headers: dict[str, str]) -> str:
    _, interview_id = setup_article(client, headers)
    path = f"/api/v1/consultant/interviews/{interview_id}"
    result = client.post(
        path + "/transcript",
        headers=headers,
        json={
            "turns": [
                {
                    "item_id": "one",
                    "speaker": "participant",
                    "text": "I solve the handoff by asking who owns the decision.",
                }
            ]
        },
    )
    assert result.status_code == 200
    result = client.post(path + "/finish", headers=headers)
    assert result.status_code == 200, result.text
    return result.json()["id"]


def test_owner_only_list_pagination_and_historical_jobs(consultant_client: TestClient) -> None:
    client = consultant_client
    client.app.state.settings.generation_performance_owner_email = "owner@example.com"
    owner = register(client, "owner")
    other = register(client, "other")
    owner_job = queue_job(client, owner)
    other_job = queue_job(client, other)
    base = "/api/v1/consultant/performance"
    assert client.get(base + "/jobs", headers=other).status_code == 403
    assert client.get(base + f"/jobs/{owner_job}", headers=other).status_code == 403
    assert client.get(base + "/access", headers=owner).status_code == 200
    first = client.get(base + "/jobs?page_size=1", headers=owner)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 2
    assert len(first.json()["items"]) == 1
    second = client.get(base + "/jobs?page_size=1&page=2", headers=owner)
    assert second.status_code == 200
    assert {first.json()["items"][0]["id"], second.json()["items"][0]["id"]} == {
        owner_job,
        other_job,
    }
    detail = client.get(base + f"/jobs/{other_job}", headers=owner).json()
    assert detail["detailed_timing_available"]
    assert detail["events"][0]["kind"] == "queue"

    async def make_historical() -> None:
        engine = create_engine(client.app.state.settings)
        try:
            async with create_session_factory(engine).begin() as db:
                await db.execute(
                    delete(ConsultantGenerationEvent).where(
                        ConsultantGenerationEvent.generation_id == UUID(other_job)
                    )
                )
                job = await db.get(ConsultantGeneration, UUID(other_job))
                assert job
                job.state = "succeeded"
                job.usage = {"total_wall_seconds": 123, "input_tokens": 100, "output_tokens": 30}
        finally:
            await engine.dispose()

    asyncio.run(make_historical())
    historical = client.get(base + f"/jobs/{other_job}", headers=owner).json()
    assert historical["detailed_timing_available"] is False
    assert historical["total_seconds"] == 123
    assert historical["total_tokens"] == 130
    assert historical["events"] == []
    completed = client.get(base + "/jobs?outcome=succeeded", headers=owner).json()
    assert completed["total"] == 1
    assert completed["median_seconds"] == 123
    assert completed["p95_seconds"] == 123
    assert client.get(base + "/jobs?request_type=ask_ai", headers=owner).json()["total"] == 0
    assert client.get(base + "/jobs?from_date=2099-01-01", headers=owner).json()["total"] == 0
    assert client.get(base + "/jobs?model=no-such-model", headers=owner).json()["total"] == 0


def test_calls_rate_limits_and_worker_recovery(consultant_client: TestClient) -> None:
    client = consultant_client
    client.app.state.settings.generation_performance_owner_email = "owner@example.com"
    owner = register(client, "owner")
    job_id = queue_job(client, owner)

    async def record() -> None:
        engine = create_engine(client.app.state.settings)
        factory = create_session_factory(engine)
        try:
            first = await claim(factory)
            assert first and first[0] == UUID(job_id)
            recorder = MetricsRecorder(factory, first[0], 1, first[1])
            call_id, started = await recorder.start("call", "write", "writer-model")
            await recorder.finish(call_id, started, "rate_limited")
            wait_id, started = await recorder.start("wait", "rate_limit", "writer-model")
            await recorder.finish(wait_id, started, "completed")
            call_id, started = await recorder.start("call", "check", "checker-model")
            await recorder.finish(call_id, started, "succeeded", 40, 9, "req-test-123")
            async with factory.begin() as db:
                job = await db.get(ConsultantGeneration, UUID(job_id))
                assert job
                job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            second = await claim(factory)
            assert second and second[0] == UUID(job_id)
            async with factory() as db:
                events = (
                    await db.scalars(
                        select(ConsultantGenerationEvent)
                        .where(ConsultantGenerationEvent.generation_id == UUID(job_id))
                        .order_by(ConsultantGenerationEvent.started_at)
                    )
                ).all()
                assert any(
                    e.kind == "attempt" and e.attempt == 1 and e.outcome == "interrupted"
                    for e in events
                )
                assert any(e.kind == "queue" and e.operation == "recovery" for e in events)
                assert any(e.kind == "attempt" and e.attempt == 2 for e in events)
        finally:
            await engine.dispose()

    asyncio.run(record())
    detail: dict[str, Any] = client.get(
        f"/api/v1/consultant/performance/jobs/{job_id}", headers=owner
    ).json()
    assert {"writer-model", "checker-model"} <= set(detail["models"])
    calls = [event for event in detail["events"] if event["kind"] == "call"]
    assert [event["outcome"] for event in calls] == ["rate_limited", "succeeded"]
    assert calls[1]["provider_request_id"] == "req-test-123"
    assert detail["total_tokens"] == 49
    assert detail["rate_limit_seconds"] is not None


def test_metrics_write_failure_cannot_save_draft(
    consultant_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = consultant_client
    client.app.state.settings.generation_performance_owner_email = "owner@example.com"
    owner = register(client, "owner")
    job_id = queue_job(client, owner)

    class FakeWriter:
        def __init__(self, *_: object) -> None:
            self.client = SimpleNamespace(close=AsyncMock())
            self.usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}

        async def write(self, *_: object) -> None:
            await self.metrics.start("call", "write", "test-model")

    async def broken_write(*_: object) -> None:
        raise RuntimeError("metrics storage unavailable")

    monkeypatch.setattr(worker, "ConsultantWriter", FakeWriter)
    monkeypatch.setattr(worker, "get_settings", lambda: client.app.state.settings)
    monkeypatch.setattr(MetricsRecorder, "start", broken_write)

    async def run() -> None:
        engine = create_engine(client.app.state.settings)
        factory = create_session_factory(engine)
        try:
            claimed = await worker.claim(factory)
            assert claimed and claimed[0] == UUID(job_id)
            await worker.process(factory, *claimed)
            async with factory() as db:
                job = await db.get(ConsultantGeneration, UUID(job_id))
                assert job and job.state == "failed"
                draft = await db.scalar(
                    select(ArticleDraft).where(ArticleDraft.article_id == job.article_id)
                )
                assert draft is None
        finally:
            await engine.dispose()

    asyncio.run(run())
