"""Consultant worker process startup tests."""

from collections.abc import Coroutine
from typing import Any

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
