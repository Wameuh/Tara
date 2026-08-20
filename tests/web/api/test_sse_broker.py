from __future__ import annotations

import asyncio

from tara_web.realtime.broker import EventBroker, RealtimeLimitExceeded


def test_broker_limits_publish_overflow_and_unsubscribe() -> None:
    broker = EventBroker(maximum=2, per_job=1)
    queue = broker.subscribe("job_one")
    with __import__("pytest").raises(RealtimeLimitExceeded):
        broker.subscribe("job_one")
    broker.publish("job_one", {"type": "snapshot_updated", "revision": 1, "data": {}})
    assert asyncio.run(queue.get())["revision"] == 1
    for revision in range(9):
        broker.publish(
            "job_one", {"type": "snapshot_updated", "revision": revision, "data": {}}
        )
    assert asyncio.run(queue.get())["revision"] == 8
    broker.unsubscribe("job_one", queue)
    assert not broker._queues
