# ruff: noqa: ANN201
"""Header-authorized SSE with snapshots as the reconnection contract."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from tara_web.api.dependencies.auth import current_job_owner, job_owner
from tara_web.api.problem_details import problem
from tara_web.api.schemas import PublicEventEnvelope
from tara_web.realtime.broker import RealtimeLimitExceeded
from tara_web.realtime.sse import event, heartbeat

router = APIRouter(prefix="/jobs", tags=["events"])


@router.get(
    "/{job_id}/events",
    response_model=None,
    responses={
        200: {
            "model": PublicEventEnvelope,
            "description": "Live lightweight job events; REST remains authoritative",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "x-event-schema": {
                        "$ref": "#/components/schemas/PublicEventEnvelope"
                    },
                }
            },
        }
    },
)
async def job_events(
    request: Request,
    job_id: str,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if job_owner(request, job_id, secret) is None:
        return problem(request, 404)
    try:
        queue = request.app.state.event_broker.subscribe(job_id)
    except RealtimeLimitExceeded:
        return problem(request, 429)

    return StreamingResponse(
        stream_job_events(request, job_id, secret, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def stream_job_events(
    request: Request,
    job_id: str,
    secret: str | None,
    queue: asyncio.Queue[dict[str, object]],
) -> AsyncIterator[bytes]:
    """Stream bounded notifications; a REST snapshot is always authoritative."""
    try:
        row = current_job_owner(request, job_id, secret)
        last_revision = -1
        if row:
            last_revision = int(row["revision"])
            yield event(
                {
                    "type": "snapshot_updated",
                    "revision": last_revision,
                    "data": {},
                }
            )
        while True:
            try:
                payload = await asyncio.wait_for(
                    queue.get(),
                    timeout=request.app.state.runtime_config.web.limits.sse_heartbeat_seconds,
                )
                current = current_job_owner(request, job_id, secret)
                if current is None:
                    return
                envelope = PublicEventEnvelope.model_validate(payload)
                last_revision = max(last_revision, envelope.revision)
                yield event(envelope.model_dump(mode="json"))
            except TimeoutError:
                current = current_job_owner(request, job_id, secret)
                if current is None:
                    return
                if int(current["revision"]) != last_revision:
                    last_revision = int(current["revision"])
                    yield event(
                        {
                            "type": "snapshot_updated",
                            "revision": last_revision,
                            "data": {},
                        }
                    )
                yield heartbeat()
            if await request.is_disconnected():
                return
    finally:
        request.app.state.event_broker.unsubscribe(job_id, queue)
