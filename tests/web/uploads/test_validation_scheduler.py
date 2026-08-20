from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from tara_web.services.validation_scheduler import ValidationScheduler


def test_scheduler_is_round_robin_and_rejects_work_after_close() -> None:
    async def scenario() -> None:
        scheduler = ValidationScheduler(concurrency=1)
        scheduler.start()
        order: list[str] = []

        def operation(name: str) -> Callable[[], Awaitable[None]]:
            async def run() -> None:
                order.append(name)
                await asyncio.sleep(0)

            return run

        scheduler.submit("session-a", operation("a1"))
        scheduler.submit("session-a", operation("a2"))
        scheduler.submit("session-b", operation("b1"))
        for _ in range(100):
            if len(order) == 3:
                break
            await asyncio.sleep(0.001)
        await scheduler.close()
        assert order == ["a1", "b1", "a2"]
        with pytest.raises(RuntimeError, match="not accepting"):
            scheduler.submit("session-a", operation("late"))

    asyncio.run(scenario())


def test_scheduler_never_exceeds_concurrency() -> None:
    async def scenario() -> None:
        scheduler = ValidationScheduler(concurrency=2)
        scheduler.start()
        active = 0
        maximum = 0
        release = asyncio.Event()

        async def operation() -> None:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await release.wait()
            active -= 1

        for index in range(5):
            scheduler.submit(f"session-{index}", operation)
        for _ in range(100):
            if maximum == 2:
                break
            await asyncio.sleep(0.001)
        assert maximum == 2
        release.set()
        await scheduler.close()

    asyncio.run(scenario())
