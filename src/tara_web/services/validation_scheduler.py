"""Fair, bounded validation scheduling primitive."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable


class ValidationScheduler:
    def __init__(self, concurrency: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._queues: dict[str, deque[Callable[[], Awaitable[None]]]] = defaultdict(
            deque
        )
        self._sessions: deque[str] = deque()
        self._task: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[None]] = set()
        self._stopping = False

    def start(self) -> None:
        if self._task is not None or self._stopping:
            raise RuntimeError("validation scheduler is already started")
        self._task = asyncio.create_task(self._run())

    def submit(self, session_id: str, operation: Callable[[], Awaitable[None]]) -> None:
        if self._task is None or self._stopping:
            raise RuntimeError("validation scheduler is not accepting work")
        if not self._queues[session_id]:
            self._sessions.append(session_id)
        self._queues[session_id].append(operation)

    async def close(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._active:
            await asyncio.gather(*tuple(self._active), return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopping:
            if not self._sessions:
                await asyncio.sleep(0.01)
                continue
            session = self._sessions.popleft()
            operation = self._queues[session].popleft()
            if self._queues[session]:
                self._sessions.append(session)
            else:
                del self._queues[session]
            await self._semaphore.acquire()
            task = asyncio.create_task(self._execute(operation))
            self._active.add(task)
            task.add_done_callback(self._active.discard)

    async def _execute(self, operation: Callable[[], Awaitable[None]]) -> None:
        try:
            await operation()
        finally:
            self._semaphore.release()
