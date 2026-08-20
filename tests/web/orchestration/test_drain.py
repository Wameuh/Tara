from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tara_web.lifecycle.drain import drain_scheduler


class _Scheduler:
    cancellation_grace_seconds = 1

    def __init__(self, active: int) -> None:
        self.pool = SimpleNamespace(active_count=active)
        self.started = False
        self.cancelled = 0

    def begin_drain(self) -> None:
        self.started = True

    async def wait_idle(self) -> None:
        while self.pool.active_count:
            await asyncio.sleep(0.01)

    def cancel_active(self) -> int:
        self.cancelled = self.pool.active_count
        self.pool.active_count = 0
        return self.cancelled


def test_drain_returns_immediately_when_idle() -> None:
    scheduler = _Scheduler(0)
    result = asyncio.run(drain_scheduler(scheduler, grace_seconds=1))  # type: ignore[arg-type]
    assert scheduler.started
    assert result.completed
    assert result.cancelled == 0


def test_drain_cancels_remaining_work_after_grace() -> None:
    scheduler = _Scheduler(2)
    result = asyncio.run(drain_scheduler(scheduler, grace_seconds=1))  # type: ignore[arg-type]
    assert not result.completed
    assert result.cancelled == 2
