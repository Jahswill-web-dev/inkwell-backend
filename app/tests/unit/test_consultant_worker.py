"""Consultant worker process startup tests."""

from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

from app.services.consultant_service import job_response
from app.workers import consultant


def test_worker_uses_selector_event_loop_policy_on_windows(monkeypatch: Any) -> None:
    selected: list[object] = []
    policy = object()

    async def fake_main() -> None:
        return None

    def fake_run(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()

    monkeypatch.setattr(consultant.sys, "platform", "win32")
    monkeypatch.setattr(consultant.asyncio, "WindowsSelectorEventLoopPolicy", lambda: policy)
    monkeypatch.setattr(consultant.asyncio, "set_event_loop_policy", selected.append)
    monkeypatch.setattr(consultant.asyncio, "run", fake_run)
    monkeypatch.setattr(consultant, "main", fake_main)

    consultant.run()

    assert selected == [policy]


def test_succeeded_job_exposes_neutral_review_fields() -> None:
    response = job_response(
        SimpleNamespace(
            id="job",
            state="succeeded",
            stage="complete",
            error=None,
            attempts=1,
            created_at=None,
            snapshot={"request_kind": "generation"},
            candidate={
                "open_finding_count": 3,
                "review_suggestions": [{"section_index": 0, "explanation": "Legacy"}],
            },
        )
    )

    assert response["writing_suggestions"] == []
    assert response["review_status"] == "clear"
    assert response["open_finding_count"] == 0
