"""Spawn entrypoint that imports Tara and provider code only in the child."""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from multiprocessing.connection import Connection

from tara.web_contracts import EventType, RunnerEvent, RunnerRequest

from .ipc import IpcMessage, encode_frame, result_payload

_DRAIN_SECONDS = 0.1


class _WorkerSink:
    """Coalesce progress locally while preserving every critical frame."""

    def __init__(self, request: RunnerRequest, token: str, writer: Connection) -> None:
        self._request = request
        self._token = token
        self._writer = writer
        self._progress: OrderedDict[str, bytes] = OrderedDict()

    def emit(self, event: RunnerEvent) -> None:
        frame = encode_frame(
            "event",
            IpcMessage(
                self._request.job_id,
                self._request.attempt_number,
                self._token,
                event,
            ).payload(),
        )
        if event.event_type == EventType.STAGE_PROGRESS:
            assert event.stage_code is not None
            self._progress[event.stage_code.value] = frame
            return
        self.flush_progress()
        self._writer.send_bytes(frame)

    def flush_progress(self) -> None:
        while self._progress:
            _, frame = self._progress.popitem(last=False)
            self._writer.send_bytes(frame)


def run_worker(
    request_payload: dict[str, object],
    worker_token: str,
    runner_kind: str,
    scenario: str,
    workspace: str,
    cancelled: object,
    event_writer: Connection,
    result_writer: Connection,
) -> None:
    """Run one isolated worker without returning technical exceptions over IPC."""
    from tara.web_contracts import runner_request_from_payload
    from tara_web.runners.factory import create_runner

    try:
        request = runner_request_from_payload(request_payload)
        os.chdir(workspace)

        class Token:
            def is_cancelled(self) -> bool:
                return bool(cancelled.is_set())

        sink = _WorkerSink(request, worker_token, event_writer)
        result = create_runner(runner_kind, scenario=scenario).run(
            request, sink, Token()
        )
        sink.flush_progress()
        result_writer.send_bytes(
            encode_frame(
                "result",
                result_payload(
                    request.job_id, request.attempt_number, worker_token, result
                ),
            )
        )
        time.sleep(_DRAIN_SECONDS)
    except BaseException:
        return
    finally:
        event_writer.close()
        result_writer.close()
