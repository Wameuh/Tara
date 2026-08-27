"""All state mutations made by the SQLite-owning process."""

from __future__ import annotations

import json
import logging
import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tara.providers.events import UsageAttempt
from tara.web_contracts import (
    CONTRACT_VERSION,
    ErrorCode,
    EventType,
    RunnerLimits,
    RunnerRequest,
    RunnerResult,
    RunnerStatus,
)
from tara_web.db.connection import ConnectionFactory
from tara_web.domain.models import utc_now
from tara_web.services.budget import BudgetService
from tara_web.services.circuit_breaker import CircuitBreaker
from tara_web.storage.artifacts import ArtifactService, validate_final_yaml_v1

from .ipc import IpcViolation, parse_message
from .job_workspace import JobWorkspaceError, JobWorkspaceService

LOGGER = logging.getLogger(__name__)


class JobService:
    def __init__(
        self,
        database: ConnectionFactory,
        *,
        max_waiting_jobs: int = 25,
        artifact_service: ArtifactService | None = None,
        workspace_service: JobWorkspaceService | None = None,
        on_change: Callable[[str], None] | None = None,
        tara_config_snapshot: str | None = None,
        runner_limits: RunnerLimits | None = None,
        budget_service: BudgetService | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        record_metrics: bool = False,
        terminal_cleanup: Callable[[str], bool] | None = None,
    ) -> None:
        self._database = database
        self.max_waiting_jobs = max_waiting_jobs
        self._artifact_service = artifact_service
        self._workspace_service = workspace_service
        self._on_change = on_change
        self._tara_config_snapshot = tara_config_snapshot
        self._runner_limits = runner_limits or RunnerLimits()
        self._budget_service = budget_service
        self._circuit_breaker = circuit_breaker
        self._record_metrics = record_metrics
        self._terminal_cleanup = terminal_cleanup

    def _notify(self, job_id: str) -> None:
        if self._on_change is not None:
            self._on_change(job_id)

    def publish_snapshot_change(self, job_id: str) -> None:
        """Called by the scheduler only after durable worker terminal mutations."""
        self._notify(job_id)

    def workspace_for(self, public_id: str) -> Path:
        """Return the private, local worker directory without serialising it."""
        return self._database.storage_root / "jobs" / public_id

    def recover(self) -> list[str]:
        """Fail orphaned active jobs and return FIFO queued identifiers."""
        with self._database.transaction() as connection:
            now = utc_now()
            interrupted = connection.execute(
                "SELECT id,public_id,current_attempt_number FROM jobs WHERE status IN "
                "('running','cancel_requested','stopping')"
            ).fetchall()
            connection.execute(
                "UPDATE jobs SET status='failed', error_code=?, "
                "error_message_key='server_interrupted', finished_at=?, "
                "updated_at=?, revision=revision+1 WHERE status IN "
                "('running','cancel_requested','stopping')",
                (ErrorCode.SERVER_INTERRUPTED.value, now, now),
            )
            connection.execute(
                "UPDATE job_attempts SET status='failed', error_code=?, "
                "finished_at=?,cost_known=CASE WHEN NOT EXISTS ("
                "SELECT 1 FROM provider_usage_attempts p "
                "WHERE p.job_id=job_attempts.job_id "
                "AND p.job_attempt_number=job_attempts.attempt_number "
                "AND p.cost_micro_eur IS NULL) THEN 1 ELSE 0 END "
                "WHERE status='running'",
                (ErrorCode.SERVER_INTERRUPTED.value, now),
            )
            for row in interrupted:
                self._reconcile_budget(
                    connection,
                    int(row["id"]),
                    str(row["public_id"]),
                    int(row["current_attempt_number"]),
                )
                self._record_terminal_metrics(connection, int(row["id"]))
        for row in interrupted:
            self.cleanup_terminal_private_data(str(row["public_id"]))
        if self._workspace_service is not None:
            for job_id in self._workspace_service.recover():
                self._fail_preparation(job_id)
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT public_id FROM jobs WHERE status='queued' "
                "ORDER BY created_at, id"
            ).fetchall()
            return [str(row[0]) for row in rows]

    def claim_next(
        self, *, max_active_jobs: int = 5
    ) -> tuple[RunnerRequest, str] | None:
        """Atomically claim one FIFO row and store the immutable worker snapshot."""
        if self._circuit_breaker is not None and not self._circuit_breaker.allow_work():
            return None
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT id,public_id,current_attempt_number,job_type,language "
                "FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        source_manifest_path: str | None = None
        merged_transcription_path: str | None = None
        context_path: str | None = None
        previous_summaries_path: str | None = None
        if row["job_type"] in {"audio", "merged_transcription"}:
            if self._workspace_service is None:
                self._fail_preparation(int(row["id"]))
                return None
            try:
                if row["job_type"] == "audio":
                    (
                        source_manifest_path,
                        context_path,
                        previous_summaries_path,
                    ) = self._workspace_service.prepare_request_paths(
                        int(row["id"]), str(row["public_id"])
                    )
                else:
                    (
                        merged_transcription_path,
                        context_path,
                        previous_summaries_path,
                    ) = self._workspace_service.prepare_merged_request_paths(
                        int(row["id"]), str(row["public_id"])
                    )
            except JobWorkspaceError:
                self._fail_preparation(int(row["id"]))
                return None
        claim, cleanup_job_id = self._commit_prepared_claim(
            row,
            max_active_jobs=max_active_jobs,
            source_manifest_path=source_manifest_path,
            merged_transcription_path=merged_transcription_path,
            context_path=context_path,
            previous_summaries_path=previous_summaries_path,
        )
        if cleanup_job_id is not None:
            self.cleanup_terminal_private_data(cleanup_job_id)
        return claim

    def _commit_prepared_claim(
        self,
        row: object,
        *,
        max_active_jobs: int,
        source_manifest_path: str | None,
        merged_transcription_path: str | None,
        context_path: str | None,
        previous_summaries_path: str | None,
    ) -> tuple[tuple[RunnerRequest, str] | None, str | None]:
        """Commit a prepared claim or durably re-arm terminal cleanup."""
        with self._database.transaction() as connection:
            current = connection.execute(
                "SELECT id,public_id,current_attempt_number,job_type,language,status "
                "FROM jobs WHERE id=?",
                (row["id"],),
            ).fetchone()
            if current is None:
                return None, None
            if current["status"] != "queued":
                if current["status"] in {
                    "completed",
                    "failed",
                    "timed_out",
                    "cancelled",
                    "cancel_failed",
                    "expired",
                    "deleted",
                }:
                    connection.execute(
                        "UPDATE jobs SET private_artifacts_cleaned_at=NULL "
                        "WHERE id=?",
                        (current["id"],),
                    )
                    return None, str(current["public_id"])
                return None, None
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN "
                "('running','cancel_requested','stopping')"
            ).fetchone()[0]
            if active >= max_active_jobs:
                return None, None
            row = current
            now = utc_now()
            cursor = connection.execute(
                "UPDATE jobs SET status='running', "
                "started_at=COALESCE(started_at,?), updated_at=?, "
                "revision=revision+1 WHERE id=? AND status='queued'",
                (now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                return None, None
            request = RunnerRequest(
                CONTRACT_VERSION,
                str(row["public_id"]),
                int(row["current_attempt_number"]),
                str(row["job_type"]),
                source_manifest_path=source_manifest_path,
                context_path=context_path,
                previous_summaries_path=previous_summaries_path,
                merged_transcription_path=merged_transcription_path,
                language=str(row["language"]),
                limits=self._runner_limits,
                tara_config_json=self._tara_config_snapshot,
            )
            token = secrets.token_urlsafe(32)
            connection.execute(
                "INSERT INTO job_run_snapshots("
                "job_id,attempt_number,request_json,claimed_at,worker_token) "
                "VALUES(?,?,?,?,?)",
                (
                    row["id"],
                    request.attempt_number,
                    json.dumps(asdict(request), default=lambda item: item.value),
                    now,
                    token,
                ),
            )
            connection.execute(
                "UPDATE job_attempts SET status='running', started_at=? "
                "WHERE job_id=? AND attempt_number=?",
                (now, row["id"], request.attempt_number),
            )
            return (request, token), None

    def _fail_preparation(self, job_id: int) -> None:
        public_id: str | None = None
        with self._database.transaction() as connection:
            now = utc_now()
            connection.execute(
                "UPDATE jobs SET status='failed',error_code='input_invalid',"
                "finished_at=?,updated_at=?,revision=revision+1 WHERE id=? "
                "AND status='queued'",
                (now, now, job_id),
            )
            connection.execute(
                "UPDATE job_attempts SET status='failed',error_code='input_invalid',"
                "finished_at=?,cost_known=1 WHERE job_id=? AND status='queued'",
                (now, job_id),
            )
            row = connection.execute(
                "SELECT public_id,current_attempt_number FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is not None:
                public_id = str(row["public_id"])
                self._reconcile_budget(
                    connection,
                    job_id,
                    str(row["public_id"]),
                    int(row["current_attempt_number"]),
                )
                self._record_terminal_metrics(connection, job_id)
        if public_id is not None:
            self.cleanup_terminal_private_data(public_id)

    def request_cancel(self, job_id: str, *, connection: object | None = None) -> bool:
        if connection is not None:
            return self._request_cancel(connection, job_id)
        with self._database.transaction() as database_connection:
            accepted = self._request_cancel(database_connection, job_id)
        if accepted:
            self.cleanup_terminal_private_data(job_id)
        return accepted

    def cleanup_terminal_private_data(self, job_id: str) -> bool:
        """Best-effort immediate cleanup; durable maintenance retries failures."""
        if self._terminal_cleanup is None:
            return True
        try:
            return self._terminal_cleanup(job_id)
        except Exception:
            LOGGER.exception("terminal_private_cleanup_failed")
            return False

    def _request_cancel(self, connection: object, job_id: str) -> bool:
        now = utc_now()
        queued = connection.execute(
            "UPDATE jobs SET status='cancelled',finished_at=?,updated_at=?,"
            "revision=revision+1 WHERE public_id=? AND status='queued'",
            (now, now, job_id),
        )
        if queued.rowcount:
            connection.execute(
                "UPDATE job_attempts SET status='cancelled',finished_at=?,cost_known=1 "
                "WHERE job_id=(SELECT id FROM jobs WHERE public_id=?) "
                "AND status='queued'",
                (now, job_id),
            )
            row = connection.execute(
                "SELECT id,current_attempt_number FROM jobs WHERE public_id=?",
                (job_id,),
            ).fetchone()
            if row is not None:
                self._reconcile_budget(
                    connection,
                    int(row["id"]),
                    job_id,
                    int(row["current_attempt_number"]),
                )
                self._record_terminal_metrics(connection, int(row["id"]))
            return True
        running = connection.execute(
            "UPDATE jobs SET status='cancel_requested',updated_at=?,"
            "revision=revision+1 WHERE public_id=? AND status='running'",
            (now, job_id),
        )
        return running.rowcount == 1

    def apply_ipc(self, payload: object) -> bool:
        try:
            message = parse_message(payload)
        except IpcViolation:
            return False
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT j.id,j.status,s.worker_token,s.last_event_revision "
                "FROM jobs j JOIN job_run_snapshots s ON s.job_id=j.id "
                "WHERE j.public_id=? AND s.attempt_number=?",
                (message.job_id, message.attempt_number),
            ).fetchone()
            if (
                row is None
                or row["worker_token"] != message.worker_token
                or row["status"] not in ("running", "cancel_requested", "stopping")
            ):
                return False
            event = message.event
            if event.revision <= row["last_event_revision"]:
                return False
            usage: UsageAttempt | None = None
            if event.event_type == EventType.USAGE_RECORDED:
                try:
                    usage = UsageAttempt.from_runner_event(event)
                except ValueError:
                    return False
            now = utc_now()
            connection.execute(
                "UPDATE job_run_snapshots SET last_event_revision=? "
                "WHERE job_id=? AND attempt_number=? AND worker_token=? "
                "AND last_event_revision < ?",
                (
                    event.revision,
                    row["id"],
                    message.attempt_number,
                    message.worker_token,
                    event.revision,
                ),
            )
            if usage is not None:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO provider_usage_attempts("
                    "attempt_id,job_id,job_attempt_number,operation_family,provider,"
                    "model,status,started_at,finished_at,input_tokens,output_tokens,"
                    "cache_tokens,duration_ms,cost_micro_eur,cost_source,"
                    "native_cost_micros,native_currency,conversion_rate,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        usage.attempt_id,
                        row["id"],
                        message.attempt_number,
                        usage.operation_family,
                        usage.provider,
                        usage.model,
                        usage.status,
                        usage.started_at,
                        usage.finished_at,
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.cache_tokens,
                        usage.duration_ms,
                        usage.cost_micro_eur,
                        usage.cost_source,
                        usage.native_cost_micros,
                        usage.native_currency,
                        usage.conversion_rate,
                        now,
                    ),
                )
                if inserted.rowcount == 1:
                    connection.execute(
                        "UPDATE job_attempts SET input_tokens=input_tokens+?,"
                        "output_tokens=output_tokens+?,"
                        "provider_cost_micro_eur=provider_cost_micro_eur+?,"
                        "has_known_cost=MAX(has_known_cost,?) "
                        "WHERE job_id=? AND attempt_number=?",
                        (
                            usage.input_tokens + usage.cache_tokens,
                            usage.output_tokens,
                            usage.cost_micro_eur or 0,
                            int(usage.cost_micro_eur is not None),
                            row["id"],
                            message.attempt_number,
                        ),
                    )
                    if self._circuit_breaker is not None:
                        if usage.status == "success":
                            self._circuit_breaker.record_success(
                                usage.provider,
                                usage.operation_family,
                                connection=connection,
                            )
                        elif usage.status in {"failed", "timed_out"}:
                            self._circuit_breaker.record_failure(
                                usage.provider,
                                usage.operation_family,
                                connection=connection,
                            )
            connection.execute(
                "INSERT INTO job_run_events("
                "job_id,attempt_number,event_revision,event_type,payload_json,"
                "created_at) VALUES(?,?,?,?,?,?)",
                (
                    row["id"],
                    message.attempt_number,
                    event.revision,
                    event.event_type.value,
                    json.dumps(event.to_payload(), separators=(",", ":")),
                    now,
                ),
            )
            if event.event_type == EventType.CANCELLATION_ACKNOWLEDGED:
                connection.execute(
                    "UPDATE jobs SET status='stopping',updated_at=?,"
                    "revision=revision+1 WHERE id=? "
                    "AND status='cancel_requested'",
                    (now, row["id"]),
                )
            elif event.event_type in {
                EventType.STAGE_STARTED,
                EventType.STAGE_PROGRESS,
                EventType.STAGE_COMPLETED,
            }:
                connection.execute(
                    "UPDATE jobs SET stage=?,substage=?,stage_progress_milli="
                    "CASE WHEN stage=? THEN MAX(stage_progress_milli,?) ELSE ? END,"
                    "total_progress_milli=MAX(total_progress_milli,?),updated_at=?,"
                    "revision=revision+1 "
                    "WHERE id=?",
                    (
                        event.stage_code.value if event.stage_code else None,
                        event.substage_code,
                        event.stage_code.value if event.stage_code else None,
                        round((event.current_ratio or 0) * 1000),
                        round((event.current_ratio or 0) * 1000),
                        round((event.overall_ratio or 0) * 1000),
                        now,
                        row["id"],
                    ),
                )
            # This is internal recovery evidence, never a public replay log.
        self._notify(message.job_id)
        return True

    def worker_lost(self, job_id: str, attempt: int, token: str, reason: str) -> bool:
        self._cleanup_staging(job_id)
        status, code = (
            ("cancel_failed", ErrorCode.CANCEL_FAILED.value)
            if reason == "cancel_failed"
            else (
                ("timed_out", ErrorCode.TIMEOUT.value)
                if reason == "timeout"
                else (
                    ("cancelled", None)
                    if reason == "cancelled"
                    else ("failed", ErrorCode.PROCESSING_FAILED.value)
                )
            )
        )
        with self._database.transaction() as connection:
            now = utc_now()
            row = connection.execute(
                "SELECT j.id,j.public_id,j.status FROM jobs j JOIN job_run_snapshots s "
                "ON s.job_id=j.id WHERE j.public_id=? AND s.attempt_number=? "
                "AND s.worker_token=?",
                (job_id, attempt, token),
            ).fetchone()
            if row is None or row["status"] not in (
                "running",
                "cancel_requested",
                "stopping",
            ):
                return False
            connection.execute(
                "UPDATE jobs SET status=?,error_code=?,finished_at=?,updated_at=?,"
                "revision=revision+1 WHERE id=?",
                (status, code, now, now, row["id"]),
            )
            connection.execute(
                "UPDATE job_attempts SET status=?,error_code=?,finished_at=? "
                "WHERE job_id=? AND attempt_number=?",
                (
                    "failed" if status == "timed_out" else status,
                    code,
                    now,
                    row["id"],
                    attempt,
                ),
            )
            self._finalize_attempt_cost(connection, int(row["id"]), attempt)
            self._reconcile_budget(
                connection, int(row["id"]), str(row["public_id"]), attempt
            )
            self._record_terminal_metrics(connection, int(row["id"]))
            if self._circuit_breaker is not None:
                self._circuit_breaker.finish_probe(
                    success=False, connection=connection
                )
        self.cleanup_terminal_private_data(job_id)
        return True

    def finish(
        self, job_id: str, attempt: int, token: str, result: RunnerResult
    ) -> bool:
        """Accept only the current worker result and promote its fixed staging file."""
        accepted = False
        with self._database.transaction() as connection:
            current = connection.execute(
                "SELECT j.id,j.status FROM jobs j JOIN job_run_snapshots s "
                "ON s.job_id=j.id "
                "WHERE j.public_id=? AND s.attempt_number=? AND s.worker_token=?",
                (job_id, attempt, token),
            ).fetchone()
            if current is None:
                self._cleanup_staging(job_id)
                return False
            if current["status"] in ("cancel_requested", "stopping"):
                self._cleanup_staging(job_id)
                accepted = self._finish_cancelled(connection, current, attempt)
            elif current["status"] != "running":
                self._cleanup_staging(job_id)
                return False
            else:
                if result.status == RunnerStatus.COMPLETED:
                    status, error = self._promote_completed(
                        int(current["id"]), job_id, result
                    )
                else:
                    self._cleanup_staging(job_id)
                    status = {
                        RunnerStatus.FAILED: "failed",
                        RunnerStatus.TIMED_OUT: "timed_out",
                        RunnerStatus.CANCELLED: "cancelled",
                        RunnerStatus.CANCEL_FAILED: "cancel_failed",
                    }[result.status]
                    error = result.error_code.value if result.error_code else None
                now = utc_now()
                cursor = connection.execute(
                    "UPDATE jobs SET status=?,error_code=?,finished_at=?,updated_at=?,"
                    "revision=revision+1 "
                    "WHERE id=? AND status='running'",
                    (status, error, now, now, current["id"]),
                )
                if cursor.rowcount != 1:
                    return False
                connection.execute(
                    "UPDATE job_attempts SET status=?,error_code=?,finished_at=? "
                    "WHERE job_id=? AND attempt_number=?",
                    (
                        status if status != "timed_out" else "failed",
                        error,
                        now,
                        current["id"],
                        attempt,
                    ),
                )
                self._finalize_attempt_cost(connection, int(current["id"]), attempt)
                self._reconcile_budget(connection, int(current["id"]), job_id, attempt)
                self._record_terminal_metrics(connection, int(current["id"]))
                if self._circuit_breaker is not None:
                    self._circuit_breaker.finish_probe(
                        success=status == "completed", connection=connection
                    )
                accepted = True
        if accepted:
            self.cleanup_terminal_private_data(job_id)
        return accepted

    def _finish_cancelled(
        self, connection: object, current: object, attempt: int
    ) -> bool:
        now = utc_now()
        connection.execute(
            "UPDATE jobs SET status='cancelled',finished_at=?,updated_at=?,"
            "revision=revision+1 WHERE id=? "
            "AND status IN ('cancel_requested','stopping')",
            (now, now, current["id"]),
        )
        connection.execute(
            "UPDATE job_attempts SET status='cancelled',finished_at=? "
            "WHERE job_id=? AND attempt_number=? AND status='running'",
            (now, current["id"], attempt),
        )
        self._finalize_attempt_cost(connection, int(current["id"]), attempt)
        public_id = connection.execute(
            "SELECT public_id FROM jobs WHERE id=?", (current["id"],)
        ).fetchone()[0]
        self._reconcile_budget(
            connection, int(current["id"]), str(public_id), attempt
        )
        self._record_terminal_metrics(connection, int(current["id"]))
        if self._circuit_breaker is not None:
            self._circuit_breaker.finish_probe(success=False, connection=connection)
        return True

    @staticmethod
    def _finalize_attempt_cost(
        connection: object, database_job_id: int, attempt: int
    ) -> None:
        """Freeze whether every recorded provider attempt has a known cost."""
        connection.execute(
            "UPDATE job_attempts SET cost_known=CASE WHEN NOT EXISTS ("
            "SELECT 1 FROM provider_usage_attempts "
            "WHERE job_id=? AND job_attempt_number=? AND cost_micro_eur IS NULL"
            ") THEN 1 ELSE 0 END WHERE job_id=? AND attempt_number=?",
            (database_job_id, attempt, database_job_id, attempt),
        )

    def _reconcile_budget(
        self,
        connection: object,
        database_job_id: int,
        public_job_id: str,
        attempt: int,
    ) -> None:
        if self._budget_service is None or not self._budget_service.enabled:
            return
        row = connection.execute(
            "SELECT provider_cost_micro_eur,cost_known FROM job_attempts "
            "WHERE job_id=? AND attempt_number=?",
            (database_job_id, attempt),
        ).fetchone()
        if row is not None:
            self._budget_service.reconcile(
                public_job_id,
                attempt,
                known_cost_micro_eur=int(row["provider_cost_micro_eur"]),
                complete=bool(row["cost_known"]),
                connection=connection,
            )

    def _record_terminal_metrics(
        self, connection: object, database_job_id: int
    ) -> None:
        if not self._record_metrics:
            return
        job = connection.execute(
            "SELECT public_id,job_type,pipeline_version,status,created_at,started_at,"
            "finished_at FROM jobs WHERE id=?",
            (database_job_id,),
        ).fetchone()
        if job is None or job["status"] not in {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
            "cancel_failed",
        }:
            return
        inputs = connection.execute(
            "SELECT COALESCE(SUM(declared_bytes),0),COUNT(*),"
            "COALESCE(SUM(duration_ms),0),COALESCE(SUM(token_count),0) "
            "FROM upload_files WHERE job_id=? AND active=1",
            (database_job_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT COUNT(*),SUM(status='failed'),SUM(status='cancelled'),"
            "COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),"
            "COALESCE(SUM(provider_cost_micro_eur),0) FROM job_attempts WHERE job_id=?",
            (database_job_id,),
        ).fetchone()
        created = _timestamp(job["created_at"])
        started = _timestamp(job["started_at"]) or created
        finished = _timestamp(job["finished_at"]) or datetime.now(UTC)
        waiting_ms = max(0, round((started - created).total_seconds() * 1000))
        execution_ms = max(0, round((finished - started).total_seconds() * 1000))
        if job["status"] != "completed":
            connection.execute(
                "INSERT INTO job_failure_metrics(date,final_status,job_count,"
                "failed_attempt_count,waiting_duration_ms,execution_duration_ms,"
                "actual_input_tokens,actual_output_tokens,actual_cost_micro_eur) "
                "VALUES(?,?,1,?,?,?,?,?,?) ON CONFLICT(date,final_status) DO UPDATE "
                "SET job_count=job_count+1,failed_attempt_count="
                "failed_attempt_count+excluded.failed_attempt_count,"
                "waiting_duration_ms=waiting_duration_ms+excluded.waiting_duration_ms,"
                "execution_duration_ms=execution_duration_ms+"
                "excluded.execution_duration_ms,actual_input_tokens="
                "actual_input_tokens+excluded.actual_input_tokens,"
                "actual_output_tokens=actual_output_tokens+excluded.actual_output_tokens,"
                "actual_cost_micro_eur=actual_cost_micro_eur+"
                "excluded.actual_cost_micro_eur",
                (
                    finished.date().isoformat(),
                    job["status"],
                    int(attempts[1] or 0),
                    waiting_ms,
                    execution_ms,
                    int(attempts[3] or 0),
                    int(attempts[4] or 0),
                    int(attempts[5] or 0),
                ),
            )
            return
        durations = _stage_durations(connection, database_job_id)
        merged_size = int(inputs[0]) if job["job_type"] == "merged_transcription" else 0
        connection.execute(
            "INSERT OR IGNORE INTO job_metrics(job_id,job_public_id,job_type,"
            "pipeline_version,created_at,completed_at,input_size_bytes,"
            "input_file_count,merged_transcription_size_bytes,"
            "merged_transcription_tokens,input_tokens,output_tokens,duration_ms,"
            "audio_duration_ms,transcription_duration_ms,validation_duration_ms,"
            "preparation_duration_ms,narrative_analysis_duration_ms,"
            "synthesis_duration_ms,verification_duration_ms,"
            "provider_cost_micro_eur,attempt_count,failed_attempt_count,"
            "cancelled_attempt_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
            ",?,?,?,?,?,?,?,?)",
            (
                database_job_id,
                job["public_id"],
                job["job_type"],
                job["pipeline_version"],
                job["created_at"],
                job["finished_at"],
                int(inputs[0]),
                int(inputs[1]),
                merged_size,
                int(inputs[3]),
                int(attempts[3]),
                int(attempts[4]),
                waiting_ms + execution_ms,
                int(inputs[2]),
                durations.get("transcription", 0),
                durations.get("input_validation", 0),
                durations.get("session_preparation", 0),
                durations.get("narrative_analysis", 0),
                durations.get("synthesis", 0),
                durations.get("verification", 0),
                int(attempts[5]),
                int(attempts[0]),
                int(attempts[1] or 0),
                int(attempts[2] or 0),
            ),
        )

    def _promote_completed(
        self, database_job_id: int, public_id: str, result: RunnerResult
    ) -> tuple[str, str | None]:
        declared = [
            artifact
            for artifact in result.artifacts
            if artifact.artifact_type.value == "final_yaml"
            and artifact.relative_path == "work/final.yaml"
            and artifact.required
        ]
        if len(declared) != 1 or self._artifact_service is None:
            self._cleanup_staging(public_id)
            return "failed", ErrorCode.ARTIFACT_WRITE_FAILED.value
        try:
            content = self._read_staging(public_id)
            validate_final_yaml_v1(content)
            outcome = self._artifact_service.write(
                job_id=database_job_id,
                artifact_type="final_yaml",
                retention_kind="final_result",
                chunks=(content,),
            )
            if outcome.error_code is None:
                return "completed", None
        except (OSError, ValueError):
            pass
        finally:
            self._cleanup_staging(public_id)
        return "failed", ErrorCode.ARTIFACT_WRITE_FAILED.value

    def _read_staging(self, public_id: str) -> bytes:
        root = self.workspace_for(public_id)
        work = root / "work"
        target = work / "final.yaml"
        for directory in (root, work):
            info = os.lstat(directory)
            if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
                raise OSError("staging directory is invalid")
        info = os.lstat(target)
        maximum = (
            self._artifact_service.policy.max_bytes if self._artifact_service else 0
        )
        if (
            not stat.S_ISREG(info.st_mode)
            or target.is_symlink()
            or info.st_size > maximum
        ):
            raise OSError("staging file is invalid")
        descriptor = os.open(
            target,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != info.st_size:
                raise OSError("staging file changed")
            chunks = bytearray()
            while len(chunks) <= maximum:
                chunk = os.read(descriptor, min(65_536, maximum + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            content = bytes(chunks)
            if len(content) != info.st_size or len(content) > maximum:
                raise OSError("staging file is invalid")
            return content
        finally:
            os.close(descriptor)

    def _cleanup_staging(self, public_id: str) -> None:
        target = self.workspace_for(public_id) / "work" / "final.yaml"
        try:
            info = os.lstat(target)
            if stat.S_ISREG(info.st_mode) and not target.is_symlink():
                target.unlink()
        except OSError:
            pass


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC)
    except ValueError:
        return None


def _stage_durations(connection: object, database_job_id: int) -> dict[str, int]:
    bounds: dict[str, tuple[datetime, datetime]] = {}
    rows = connection.execute(
        "SELECT payload_json,created_at FROM job_run_events WHERE job_id=? "
        "AND event_type IN ('stage_started','stage_progress','stage_completed') "
        "ORDER BY id",
        (database_job_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
            stage = payload.get("stage_code")
            occurred = _timestamp(row["created_at"])
            if not isinstance(stage, str) or occurred is None:
                continue
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        first, _ = bounds.get(stage, (occurred, occurred))
        bounds[stage] = (first, occurred)
    return {
        stage: max(0, round((last - first).total_seconds() * 1000))
        for stage, (first, last) in bounds.items()
    }
