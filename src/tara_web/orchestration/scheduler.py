"""Single-owner durable FIFO dispatcher."""

from __future__ import annotations

import asyncio

from .job_service import JobService
from .process_pool import ProcessPool


class Scheduler:
    def __init__(
        self,
        service: JobService,
        *,
        max_active_jobs: int = 5,
        ipc_maximum: int = 512,
        job_timeout_seconds: int = 3600,
        cancellation_grace_seconds: int = 15,
        runner_kind: str = "fake",
        fake_scenario: str = "success",
    ) -> None:
        self.service = service
        self.pool = ProcessPool(max_active_jobs, ipc_maximum)
        self.max_active_jobs = max_active_jobs
        self.job_timeout_seconds = job_timeout_seconds
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self.runner_kind = runner_kind
        self.fake_scenario = fake_scenario
        self._task: asyncio.Task[None] | None = None
        self._claim_task: asyncio.Task[tuple[object, str] | None] | None = None
        self._claim_requested = False
        self._closed = False
        self._wakeup = asyncio.Event()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("scheduler is closed")
        if self._task is not None:
            return
        queued_jobs = self.service.recover()
        self._claim_requested = bool(queued_jobs)
        self._task = asyncio.create_task(self._run())
        self._wakeup.set()

    def wakeup(self) -> None:
        if not self._closed:
            self._claim_requested = True
            self._wakeup.set()

    def cancel(self, job_id: str) -> bool:
        if self._closed:
            return False
        accepted = self.service.request_cancel(job_id)
        if accepted:
            self.pool.cancel(job_id)
            self._wakeup.set()
        return accepted

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._claim_task is not None:
            await asyncio.shield(self._claim_task)
        self.pool.close()

    async def _run(self) -> None:
        while True:
            for payload in self.pool.take_events():
                self.service.apply_ipc(payload)
            for task, result, reason in self.pool.finished(
                timeout_seconds=self.job_timeout_seconds,
                cancellation_grace_seconds=self.cancellation_grace_seconds,
            ):
                if result is not None and reason is None:
                    self.service.finish(
                        task.job_id, task.attempt_number, task.worker_token, result
                    )
                else:
                    self.service.worker_lost(
                        task.job_id,
                        task.attempt_number,
                        task.worker_token,
                        reason or "worker_crashed",
                    )
                self.service.publish_snapshot_change(task.job_id)
                self._claim_requested = True
            while self.pool.active_count < self.max_active_jobs:
                if not self._claim_requested:
                    break
                if self._claim_task is None:
                    self._claim_task = asyncio.create_task(
                        asyncio.to_thread(
                            self.service.claim_next,
                            max_active_jobs=self.max_active_jobs,
                        )
                    )
                try:
                    claim = await asyncio.shield(self._claim_task)
                finally:
                    if self._claim_task is not None and self._claim_task.done():
                        self._claim_task = None
                if claim is None:
                    self._claim_requested = False
                    break
                request, token = claim
                self.pool.submit(
                    request,
                    token,
                    self.fake_scenario,
                    self.service.workspace_for(request.job_id),
                    runner_kind=self.runner_kind,
                )
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=0.1)
                self._wakeup.clear()
            except TimeoutError:
                pass
