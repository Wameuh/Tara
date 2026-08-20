"""Job promotion, revisions and telemetry aggregates."""

from __future__ import annotations

from datetime import date

from tara.token_limits import TokenLimitExceeded, require_token_limit
from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.domain.models import Job, JobMetricsRecord, utc_now


class JobRepository:
    def __init__(self, factory: ConnectionFactory) -> None:
        self._factory = factory

    def promote(
        self,
        job: Job,
        *,
        expected_session_revision: int,
        max_waiting_jobs: int = 25,
        connection: object | None = None,
    ) -> None:
        """Claim exactly one ready session and create its initial attempt atomically."""
        if connection is not None:
            self._promote(connection, job, expected_session_revision, max_waiting_jobs)
            return
        with self._factory.transaction() as database_connection:
            self._promote(
                database_connection, job, expected_session_revision, max_waiting_jobs
            )

    @staticmethod
    def _promote(
        connection: object,
        job: Job,
        expected_session_revision: int,
        max_waiting_jobs: int,
    ) -> None:
        queued = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'queued'"
        ).fetchone()[0]
        if (
            not isinstance(max_waiting_jobs, int)
            or isinstance(max_waiting_jobs, bool)
            or not 0 <= max_waiting_jobs <= 10_000
        ):
            raise ValueError("waiting job limit is invalid")
        if queued >= max_waiting_jobs:
            raise DatabaseConflict("job queue is at capacity")
        session = connection.execute(
            "SELECT id,secret_hmac,secret_generation,relaunch_parent_job_id,"
            "context_text,previous_summaries_text,input_type "
            "FROM upload_sessions "
            "WHERE public_id = ? "
            "AND status = 'ready' AND revision = ?",
            (job.session_public_id, expected_session_revision),
        ).fetchone()
        if session is None:
            raise DatabaseConflict("resource revision conflict")
        if job.job_type != session["input_type"]:
            raise DatabaseConflict("job input type does not match session")
        try:
            require_token_limit(str(session["context_text"] or ""), 2_000)
            require_token_limit(str(session["previous_summaries_text"] or ""), 50_000)
        except TokenLimitExceeded as exc:
            raise DatabaseConflict("session text exceeds token limit") from exc
        files = connection.execute(
            "SELECT COUNT(*) AS total, SUM(status = 'ready') AS ready "
            "FROM upload_files WHERE session_id = ? AND active=1",
            (session["id"],),
        ).fetchone()
        if session["input_type"] == "merged_transcription" and files["total"] != 1:
            raise DatabaseConflict("merged transcription requires exactly one file")
        if files["total"] == 0 or files["total"] != files["ready"]:
            raise DatabaseConflict("upload files are not ready")
        if job.secret_hmac != session["secret_hmac"]:
            raise DatabaseConflict("job secret does not match session")
        timestamp = utc_now()
        parent_id = session["relaunch_parent_job_id"]
        root_id = None
        if parent_id is not None:
            lineage = connection.execute(
                "SELECT id,COALESCE(root_job_id,id) AS root_job_id "
                "FROM jobs WHERE id=?",
                (parent_id,),
            ).fetchone()
            if lineage is None:
                raise DatabaseConflict("relaunch parent unavailable")
            parent_id, root_id = lineage["id"], lineage["root_job_id"]
        connection.execute(
            "INSERT INTO jobs (public_id,upload_session_id,secret_hmac,"
            "secret_generation,status,pipeline_version,job_type,language,"
            "expires_at,input_file_count,created_at,updated_at,"
            "parent_job_id,root_job_id) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.public_id,
                session["id"],
                session["secret_hmac"],
                session["secret_generation"],
                job.pipeline_version,
                job.job_type,
                job.language,
                job.expires_at,
                files["total"],
                timestamp,
                timestamp,
                parent_id,
                root_id,
            ),
        )
        connection.execute(
            "INSERT INTO job_attempts (job_id, attempt_number, status) "
            "SELECT id, 1, 'queued' FROM jobs WHERE public_id = ?",
            (job.public_id,),
        )
        connection.execute(
            "UPDATE upload_files SET job_id = ? WHERE session_id = ? "
            "AND job_id IS NULL AND active=1 AND status='ready'",
            (
                connection.execute(
                    "SELECT id FROM jobs WHERE public_id = ?", (job.public_id,)
                ).fetchone()[0],
                session["id"],
            ),
        )
        affected = connection.execute("SELECT changes()").fetchone()[0]
        if affected != files["total"]:
            raise DatabaseConflict("upload files are not ready")
        cursor = connection.execute(
            "UPDATE upload_sessions SET status = 'consumed', consumed_at = ?, "
            "revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?",
            (timestamp, timestamp, session["id"], expected_session_revision),
        )
        if cursor.rowcount != 1:
            raise DatabaseConflict("resource revision conflict")

    def record_job_metrics(self, metrics: JobMetricsRecord) -> None:
        """Persist exactly one successful-job regression sample."""
        with self._factory.transaction() as connection:
            job = connection.execute(
                "SELECT id, public_id, job_type, pipeline_version, created_at, status "
                "FROM jobs WHERE public_id = ?",
                (metrics.job_public_id,),
            ).fetchone()
            if job is None or job["status"] != "completed":
                raise DatabaseConflict("completed job is unavailable")
            cursor = connection.execute(
                "INSERT INTO job_metrics (job_id,job_public_id,job_type,"
                "pipeline_version,created_at,completed_at,input_size_bytes,input_file_count,"
                "merged_transcription_size_bytes,merged_transcription_tokens,input_tokens,"
                "output_tokens,duration_ms,audio_duration_ms,transcription_duration_ms,"
                "validation_duration_ms,preparation_duration_ms,narrative_analysis_duration_ms,"
                "synthesis_duration_ms,verification_duration_ms,provider_cost_micro_eur,"
                "attempt_count,failed_attempt_count,cancelled_attempt_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(job_public_id) DO NOTHING",
                (
                    job["id"],
                    job["public_id"],
                    job["job_type"],
                    job["pipeline_version"],
                    job["created_at"],
                    utc_now(),
                    metrics.input_size_bytes,
                    metrics.input_file_count,
                    metrics.merged_transcription_size_bytes,
                    metrics.merged_transcription_tokens,
                    metrics.input_tokens,
                    metrics.output_tokens,
                    metrics.duration_ms,
                    metrics.audio_duration_ms,
                    metrics.transcription_duration_ms,
                    metrics.validation_duration_ms,
                    metrics.preparation_duration_ms,
                    metrics.narrative_analysis_duration_ms,
                    metrics.synthesis_duration_ms,
                    metrics.verification_duration_ms,
                    metrics.provider_cost_micro_eur,
                    metrics.attempt_count,
                    metrics.failed_attempt_count,
                    metrics.cancelled_attempt_count,
                ),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("job metrics already exist")

    def record_failure_metrics(
        self,
        *,
        day: str,
        final_status: str,
        job_count: int,
        failed_attempt_count: int,
        waiting_duration_ms: int,
        execution_duration_ms: int,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        actual_input_tokens: int = 0,
        actual_output_tokens: int = 0,
        estimated_cost_micro_eur: int = 0,
        actual_cost_micro_eur: int = 0,
    ) -> None:
        if final_status not in {"failed", "timed_out", "cancelled", "cancel_failed"}:
            raise ValueError("failure outcome is invalid")
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError as exc:
            raise ValueError("metric day is invalid") from exc
        values = (
            job_count,
            failed_attempt_count,
            waiting_duration_ms,
            execution_duration_ms,
            estimated_input_tokens,
            estimated_output_tokens,
            actual_input_tokens,
            actual_output_tokens,
            estimated_cost_micro_eur,
            actual_cost_micro_eur,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 10**15
            for value in values
        ):
            raise ValueError("failure metrics are invalid")
        with self._factory.transaction() as connection:
            connection.execute(
                "INSERT INTO job_failure_metrics(date,final_status,job_count,"
                "failed_attempt_count,waiting_duration_ms,execution_duration_ms,"
                "estimated_input_tokens,estimated_output_tokens,actual_input_tokens,"
                "actual_output_tokens,estimated_cost_micro_eur,actual_cost_micro_eur) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(date,final_status) DO UPDATE SET "
                "job_count=job_count+excluded.job_count, "
                "failed_attempt_count=failed_attempt_count+"
                "excluded.failed_attempt_count, "
                "waiting_duration_ms=waiting_duration_ms+"
                "excluded.waiting_duration_ms, "
                "execution_duration_ms=execution_duration_ms+"
                "excluded.execution_duration_ms, "
                "estimated_input_tokens=estimated_input_tokens+"
                "excluded.estimated_input_tokens, "
                "estimated_output_tokens=estimated_output_tokens+"
                "excluded.estimated_output_tokens, "
                "actual_input_tokens=actual_input_tokens+"
                "excluded.actual_input_tokens, "
                "actual_output_tokens=actual_output_tokens+"
                "excluded.actual_output_tokens, "
                "estimated_cost_micro_eur=estimated_cost_micro_eur+"
                "excluded.estimated_cost_micro_eur, "
                "actual_cost_micro_eur=actual_cost_micro_eur+excluded.actual_cost_micro_eur",
                (day, final_status, *values),
            )

    def compare_and_set_status(
        self, public_id: str, expected_revision: int, status: str
    ) -> int:
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, revision = revision + 1, updated_at = ? "
                "WHERE public_id = ? AND revision = ?",
                (status, utc_now(), public_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("resource revision conflict")
            return int(
                connection.execute(
                    "SELECT revision FROM jobs WHERE public_id = ?", (public_id,)
                ).fetchone()[0]
            )

    def record_pre_job_error(
        self, error_code: str, input_type: str, *, day: str | None = None
    ) -> None:
        if day is None:
            raise ValueError("metric day is required")
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError as exc:
            raise ValueError("metric day is invalid") from exc
        if not 1 <= len(error_code) <= 64 or input_type not in {
            "audio",
            "merged_transcription",
            "context",
            "previous_summaries",
            "request",
        }:
            raise ValueError("pre-job metric is invalid")
        metric_day = day
        with self._factory.transaction() as connection:
            connection.execute(
                "INSERT INTO pre_job_error_metrics(date, error_code, input_type, "
                "count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(date, error_code, input_type) "
                "DO UPDATE SET count = count + 1",
                (metric_day, error_code, input_type),
            )
