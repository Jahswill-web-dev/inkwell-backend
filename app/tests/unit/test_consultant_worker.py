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


def test_source_fidelity_message_shows_bounded_checker_feedback() -> None:
    message = consultant.source_fidelity_message(
        [
            "  This claim is not supported\nby the interview.  ",
            "This claim is not supported by the interview.",
            "The example is attributed to the consultant without evidence.",
            "The quotation is not verbatim.",
            "A statistic lacks a source.",
            "A fifth distinct issue should not appear.",
        ]
    )

    assert "1. This claim is not supported by the interview." in message
    assert message.count("This claim is not supported by the interview.") == 1
    assert "The quotation is not verbatim." in message
    assert "A fifth distinct issue" not in message
    assert message.endswith("Please review these points before retrying.")
