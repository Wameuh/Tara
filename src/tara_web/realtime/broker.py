"""Bounded in-memory fan-out, intentionally not an event replay journal."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from tara_web.api.schemas import PublicEventEnvelope


class RealtimeLimitExceeded(RuntimeError):
    pass


class EventBroker:
    def __init__(self, *, maximum: int, per_job: int = 4) -> None:
        self.maximum = maximum
        self.per_job = per_job
        self._queues: dict[str, set[asyncio.Queue[dict[str, object]]]] = defaultdict(
            set
        )

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, object]]:
        if (
            sum(map(len, self._queues.values())) >= self.maximum
            or len(self._queues[job_id]) >= self.per_job
        ):
            raise RealtimeLimitExceeded
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=8)
        self._queues[job_id].add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict[str, object]]) -> None:
        queues = self._queues.get(job_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self._queues.pop(job_id, None)

    def publish(self, job_id: str, event: dict[str, object]) -> None:
        event = PublicEventEnvelope.model_validate(event).model_dump(mode="json")
        for queue in tuple(self._queues.get(job_id, ())):
            if queue.full():
                # One bounded marker tells the client to re-read its REST snapshot.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(
                    {
                        "type": "snapshot_updated",
                        "revision": event["revision"],
                        "data": {},
                    }
                )
                continue
            queue.put_nowait(event)
