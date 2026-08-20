"""Short, conditional persistence operations for managed job artifacts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.domain.models import utc_now
from tara_web.storage.layout import ARTIFACT_TYPES, StorageLayout

_RETENTION = {"intermediate": timedelta(hours=24), "final_result": timedelta(days=7)}
_ERROR_CODES = {
    "write_failed",
    "integrity_failed",
    "delete_failed",
}
_HEX = re.compile(r"^[0-9a-f]{64}$")


class ArtifactRepository:
    def __init__(self, factory: ConnectionFactory) -> None:
        self._factory = factory

    def create_pending(
        self,
        *,
        job_id: int,
        artifact_type: str,
        retention_kind: str,
        relative_path: str,
        original_filename: str | None,
        max_per_job: int,
    ) -> int:
        if artifact_type not in ARTIFACT_TYPES or retention_kind not in _RETENTION:
            raise ValueError("artifact attributes are invalid")
        if (artifact_type == "final_yaml") != (retention_kind == "final_result"):
            raise ValueError("artifact retention is invalid")
        if not isinstance(max_per_job, int) or not 1 <= max_per_job <= 10_000:
            raise ValueError("artifact limit is invalid")
        if original_filename is not None and (
            len(original_filename.encode("utf-8")) > 255
            or any(ord(character) < 32 for character in original_filename)
        ):
            raise ValueError("artifact filename is invalid")
        with self._factory.transaction() as connection:
            job = connection.execute(
                "SELECT public_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise DatabaseConflict("job is unavailable")
            managed = StorageLayout(self._factory.storage_root).parse_artifact_path(
                relative_path
            )
            if managed.directory_parts[1] != job[
                "public_id"
            ] or managed.directory_parts[2] != StorageLayout(
                self._factory.storage_root
            ).area_for(artifact_type):
                raise DatabaseConflict("artifact ownership conflict")
            count = connection.execute(
                "SELECT COUNT(*) FROM job_artifacts WHERE job_id = ? "
                "AND storage_state != 'deleted'",
                (job_id,),
            ).fetchone()[0]
            if count >= max_per_job:
                raise DatabaseConflict("artifact limit reached")
            now = datetime.now(UTC)
            cursor = connection.execute(
                "INSERT INTO job_artifacts(job_id,artifact_type,retention_kind,"
                "storage_state,relative_path,original_filename,expires_at,"
                "created_at,updated_at) VALUES (?,?,?,'pending',?,?,?,?,?)",
                (
                    job_id,
                    artifact_type,
                    retention_kind,
                    relative_path,
                    original_filename,
                    (now + _RETENTION[retention_kind]).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def stage(
        self,
        artifact_id: int,
        *,
        byte_size: int,
        sha256_hex: str | None,
        temporary_name: str,
    ) -> None:
        self._validate_metadata(byte_size, sha256_hex, temporary_name)
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE job_artifacts SET storage_state='staged',byte_size=?,"
                "sha256_hex=?,staged_temp_name=?,updated_at=? WHERE id=? "
                "AND storage_state='pending'",
                (byte_size, sha256_hex, temporary_name, utc_now(), artifact_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("artifact state conflict")

    def mark_ready(self, artifact_id: int) -> None:
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE job_artifacts SET storage_state='ready',"
                "staged_temp_name=NULL,updated_at=? WHERE id=? "
                "AND storage_state='staged' AND byte_size IS NOT NULL "
                "AND (artifact_type != 'final_yaml' OR sha256_hex IS NOT NULL)",
                (utc_now(), artifact_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("artifact state conflict")

    def mark_error(
        self, artifact_id: int, code: str, *, integrity_only: bool = False
    ) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("artifact error code is invalid")
        allowed = "'ready'" if integrity_only else "'pending','staged','deleting'"
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE job_artifacts SET storage_state='error',"
                "storage_error_code=?,updated_at=? WHERE id=? AND storage_state IN ("
                + allowed
                + ")",
                (code, utc_now(), artifact_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("artifact state conflict")

    def begin_delete_attempt(self, artifact_id: int) -> str | None:
        with self._factory.transaction() as connection:
            row = connection.execute(
                "SELECT storage_state,relative_path FROM job_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise DatabaseConflict("artifact is unavailable")
            if row["storage_state"] == "deleted":
                return None
            if row["storage_state"] not in {"ready", "error", "deleting"}:
                raise DatabaseConflict("artifact state conflict")
            cursor = connection.execute(
                "UPDATE job_artifacts SET storage_state='deleting',"
                "delete_attempt_count=delete_attempt_count+1,"
                "last_delete_attempt_at=?,updated_at=? WHERE id=? "
                "AND storage_state IN ('ready','error','deleting')",
                (utc_now(), utc_now(), artifact_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("artifact state conflict")
            return str(row["relative_path"])

    def mark_deleted(self, artifact_id: int) -> None:
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE job_artifacts SET storage_state='deleted',deleted_at=?,"
                "original_filename=NULL,staged_temp_name=NULL,updated_at=? "
                "WHERE id=? AND storage_state='deleting'",
                (utc_now(), utc_now(), artifact_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("artifact state conflict")

    def get_final_ready(self, *, artifact_id: int, job_id: int) -> dict[str, object]:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT id,relative_path,sha256_hex,byte_size FROM job_artifacts "
                "WHERE id=? AND job_id=? AND artifact_type='final_yaml' "
                "AND storage_state='ready'",
                (artifact_id, job_id),
            ).fetchone()
            if row is None:
                raise DatabaseConflict("result is unavailable")
            return dict(row)
        finally:
            connection.close()

    def get_latest_ready(
        self, *, job_id: int, artifact_type: str
    ) -> dict[str, object] | None:
        """Return only the newest ready managed artifact of the requested type."""
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError("artifact type is invalid")
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT id,relative_path,sha256_hex,byte_size FROM job_artifacts "
                "WHERE job_id=? AND artifact_type=? AND storage_state='ready' "
                "ORDER BY id DESC LIMIT 1",
                (job_id, artifact_type),
            ).fetchone()
            return None if row is None else dict(row)
        finally:
            connection.close()

    def reconciliation_batch(self, limit: int) -> list[dict[str, object]]:
        return self._rows("storage_state IN ('pending','staged','deleting')", (), limit)

    def get_job_public_id(self, job_id: int) -> str:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT public_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise DatabaseConflict("job is unavailable")
            return str(row[0])
        finally:
            connection.close()

    def expired_batch(self, now: str, limit: int) -> list[dict[str, object]]:
        return self._rows(
            "storage_state IN ('ready','error') "
            "AND julianday(expires_at) <= julianday(?)",
            (now,),
            limit,
        )

    def path_exists(self, relative_path: str) -> bool:
        connection = self._factory.connect()
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM job_artifacts WHERE relative_path=? UNION ALL "
                    "SELECT 1 FROM job_input_preparations p JOIN jobs j "
                    "ON j.id=p.job_id WHERE j.status != 'deleted' "
                    "AND p.destination_path=? "
                    "UNION ALL SELECT 1 FROM jobs WHERE status != 'deleted' AND "
                    "'jobs/' || public_id || '/inputs/source-manifest.json'=? LIMIT 1",
                    (relative_path, relative_path, relative_path),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    def staged_temp_is_referenced(
        self, relative_path: str, temporary_name: str
    ) -> bool:
        connection = self._factory.connect()
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM job_artifacts WHERE relative_path = ? "
                    "AND staged_temp_name = ? AND storage_state = 'staged' LIMIT 1",
                    (relative_path, temporary_name),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    def _rows(
        self, predicate: str, values: tuple[object, ...], limit: int
    ) -> list[dict[str, object]]:
        if not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise ValueError("artifact batch is invalid")
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM job_artifacts WHERE "
                + predicate
                + " ORDER BY id LIMIT ?",
                (*values, limit),
            )
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def _validate_metadata(
        self, byte_size: int, sha256_hex: str | None, temporary_name: str
    ) -> None:
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or not 0 <= byte_size <= 10**15
        ):
            raise ValueError("artifact metadata is invalid")
        if sha256_hex is not None and not _HEX.fullmatch(sha256_hex):
            raise ValueError("artifact metadata is invalid")
        if not re.fullmatch(
            r"\.[a-f0-9]{32}\.(?:bin|yaml)\.[a-f0-9]{32}\.tmp", temporary_name
        ):
            raise ValueError("artifact metadata is invalid")
