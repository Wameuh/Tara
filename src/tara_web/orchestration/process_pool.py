"""Spawn-safe process supervisor; child processes have no database capability."""

from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path

from tara.web_contracts import (
    MAX_IPC_EVENT_BYTES,
    RunnerRequest,
    RunnerResult,
)

from .ipc import (
    IpcViolation,
    parse_frame,
    parse_message,
    result_payload,
)
from .worker_entrypoint import run_worker

_DRAIN_SECONDS = 0.1


@dataclass(slots=True)
class RunningTask:
    job_id: str
    attempt_number: int
    worker_token: str
    process: mp.Process
    cancellation: object
    event_reader: Connection
    result_reader: Connection
    started_at: float
    cancellation_started_at: float | None = None
    cancellation_reason: str | None = None


class ProcessPool:
    def __init__(self, max_workers: int, ipc_maximum: int = 512) -> None:
        if not 1 <= max_workers <= 32 or not 16 <= ipc_maximum <= 16_384:
            raise ValueError("process pool limits are invalid")
        self._context = mp.get_context("spawn")
        self._max_workers = max_workers
        self._tasks: dict[str, RunningTask] = {}
        self._results: dict[tuple[str, int, str], RunnerResult] = {}
        self._closed = False

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    @property
    def active_job_ids(self) -> tuple[str, ...]:
        return tuple(self._tasks)

    def submit(
        self,
        request: RunnerRequest,
        worker_token: str,
        scenario: str,
        workspace: Path,
        *,
        runner_kind: str = "fake",
    ) -> RunningTask:
        if (
            self._closed
            or self.active_count >= self._max_workers
            or request.job_id in self._tasks
        ):
            raise RuntimeError("process pool is unavailable")
        technical_root = workspace.resolve(strict=True)
        if not technical_root.is_dir() or technical_root.is_symlink():
            raise RuntimeError("worker workspace is unavailable")
        event_reader, event_writer = self._context.Pipe(duplex=False)
        result_reader, result_writer = self._context.Pipe(duplex=False)
        cancelled = self._context.Event()
        process = self._context.Process(
            target=run_worker,
            args=(
                request.to_payload(),
                worker_token,
                runner_kind,
                scenario,
                str(technical_root),
                cancelled,
                event_writer,
                result_writer,
            ),
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            event_reader.close()
            event_writer.close()
            result_reader.close()
            result_writer.close()
            raise
        event_writer.close()
        result_writer.close()
        task = RunningTask(
            request.job_id,
            request.attempt_number,
            worker_token,
            process,
            cancelled,
            event_reader,
            result_reader,
            time.monotonic(),
        )
        self._tasks[request.job_id] = task
        return task

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None or self._closed:
            return False
        self._begin_cancellation(task, "cancel")
        return True

    def take_events(self) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for task in self._tasks.values():
            while self._ready(task.event_reader):
                try:
                    kind, payload = parse_frame(
                        task.event_reader.recv_bytes(MAX_IPC_EVENT_BYTES)
                    )
                    if kind != "event":
                        continue
                    parse_message(payload)
                    values.append(payload)
                except (EOFError, OSError, IpcViolation):
                    break
        return values

    def finished(
        self, *, timeout_seconds: int, cancellation_grace_seconds: int
    ) -> list[tuple[RunningTask, RunnerResult | None, str | None]]:
        for task in list(self._tasks.values()):
            self._drain_result(task, wait_seconds=0)
        now = time.monotonic()
        completed: list[tuple[RunningTask, RunnerResult | None, str | None]] = []
        for job_id, task in list(self._tasks.items()):
            if (
                task.cancellation_reason is None
                and now - task.started_at >= timeout_seconds
            ):
                self._begin_cancellation(task, "timeout")
                continue
            reason: str | None = None
            if (
                task.cancellation_started_at is not None
                and now - task.cancellation_started_at >= cancellation_grace_seconds
            ):
                reason = (
                    "timeout"
                    if task.cancellation_reason == "timeout"
                    else "cancel_failed"
                )
            if reason is None and task.process.is_alive():
                continue
            self._drain_result(task, wait_seconds=_DRAIN_SECONDS)
            if task.process.is_alive():
                task.process.terminate()
            task.process.join(timeout=1)
            key = (task.job_id, task.attempt_number, task.worker_token)
            result = self._results.pop(key, None)
            if reason is None and result is None:
                reason = "worker_crashed"
            elif task.cancellation_reason == "timeout":
                result = None
                reason = "timeout"
            completed.append((task, result, reason))
            self._close_task(task)
            self._tasks.pop(job_id, None)
        return completed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks.values():
            task.cancellation.set()
        deadline = time.monotonic() + 2
        for task in self._tasks.values():
            task.process.join(max(0, deadline - time.monotonic()))
            if task.process.is_alive():
                task.process.terminate()
                task.process.join(1)
            self._close_task(task)
        self._tasks.clear()

    def _drain_result(self, task: RunningTask, *, wait_seconds: float) -> None:
        deadline = time.monotonic() + wait_seconds
        while self._ready(task.result_reader, max(0, deadline - time.monotonic())):
            try:
                kind, payload = parse_frame(
                    task.result_reader.recv_bytes(MAX_IPC_EVENT_BYTES)
                )
                if kind != "result":
                    continue
                item = result_payload.parse(payload)
                self._results[(item.job_id, item.attempt_number, item.worker_token)] = (
                    item.result
                )
            except (EOFError, OSError, IpcViolation):
                return

    @staticmethod
    def _close_task(task: RunningTask) -> None:
        task.event_reader.close()
        task.result_reader.close()

    @staticmethod
    def _ready(connection: Connection, timeout: float = 0) -> bool:
        try:
            return connection.poll(timeout)
        except (EOFError, OSError):
            return False

    @staticmethod
    def _begin_cancellation(task: RunningTask, reason: str) -> None:
        task.cancellation.set()
        task.cancellation_started_at = task.cancellation_started_at or time.monotonic()
        task.cancellation_reason = task.cancellation_reason or reason
