"""Graceful scheduler drain without accepting additional work."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from tara_web.orchestration.scheduler import Scheduler


@dataclass(frozen=True)
class DrainResult:
    completed: bool
    cancelled: int


async def drain_scheduler(scheduler: Scheduler, *, grace_seconds: int) -> DrainResult:
    """Stop claims, wait for active jobs, then request cooperative cancellation."""
    if not isinstance(grace_seconds, int) or isinstance(grace_seconds, bool):
        raise ValueError("shutdown grace period is invalid")
    if not 1 <= grace_seconds <= 3_600:
        raise ValueError("shutdown grace period is invalid")
    scheduler.begin_drain()
    try:
        await asyncio.wait_for(scheduler.wait_idle(), timeout=grace_seconds)
        return DrainResult(True, 0)
    except TimeoutError:
        cancelled = scheduler.cancel_active()
        try:
            await asyncio.wait_for(
                scheduler.wait_idle(),
                timeout=scheduler.cancellation_grace_seconds + 1,
            )
        except TimeoutError:
            pass
        return DrainResult(False, cancelled)
