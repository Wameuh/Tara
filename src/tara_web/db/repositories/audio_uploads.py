"""Transactional persistence for direct resumable audio uploads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tara_web.db.connection import ConnectionFactory, DatabaseConflict


class AudioUploadRepository:
    def __init__(self, factory: ConnectionFactory) -> None:
        self._factory = factory

    def create_session(
        self,
        public_id: str,
        secret_hmac: str,
        *,
        max_sessions: int,
        input_type: str = "audio",
        expires_hours: int = 24,
    ) -> None:
        if input_type not in {"audio", "merged_transcription", "zip"}:
            raise DatabaseConflict("upload input type is invalid")
        archive_mode = input_type == "zip"
        stored_input_type = "audio" if archive_mode else input_type
        now = datetime.now(UTC)
        with self._factory.transaction() as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM upload_sessions WHERE status NOT IN "
                "('consumed','cancelled','expired') "
                "AND julianday(expires_at)>julianday(?)",
                (now.isoformat(),),
            ).fetchone()[0]
            if active >= max_sessions:
                raise DatabaseConflict("upload session limit reached")
            connection.execute(
                "INSERT INTO upload_sessions("
                "public_id,secret_hmac,status,expires_at,created_at,updated_at,"
                "last_activity_at,input_type,archive_mode,archive_phase) "
                "VALUES (?,?,'created',?,?,?,?,?,?,?)",
                (
                    public_id,
                    secret_hmac,
                    (now + timedelta(hours=expires_hours)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    stored_input_type,
                    int(archive_mode),
                    "transfer" if archive_mode else None,
                ),
            )

    def create_relaunch_session(
        self,
        public_id: str,
        secret_hmac: str,
        *,
        parent_job_id: int,
        connection: object | None = None,
    ) -> None:
        if connection is not None:
            self._create_relaunch_session(
                connection, public_id, secret_hmac, parent_job_id
            )
            return
        with self._factory.transaction() as database_connection:
            self._create_relaunch_session(
                database_connection, public_id, secret_hmac, parent_job_id
            )

    @staticmethod
    def _create_relaunch_session(
        connection: object, public_id: str, secret_hmac: str, parent_job_id: int
    ) -> None:
        now = datetime.now(UTC)
        parent = connection.execute(
            "SELECT id,job_type FROM jobs WHERE id=? AND status IN "
            "('failed','timed_out','cancelled','cancel_failed')",
            (parent_job_id,),
        ).fetchone()
        if parent is None:
            raise DatabaseConflict("job cannot be relaunched")
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at,last_activity_at,relaunch_parent_job_id,input_type) "
            "VALUES (?,?,'created',?,?,?,?,?,?)",
            (
                public_id,
                secret_hmac,
                (now + timedelta(hours=24)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                parent_job_id,
                parent["job_type"],
            ),
        )

    def session(self, public_id: str) -> dict[str, object] | None:
        return self._one(
            "SELECT s.*,(SELECT COALESCE(MAX(f.archive_excluded_count),0) "
            "FROM upload_files f WHERE f.session_id=s.id) AS "
            "archive_excluded_count FROM upload_sessions s WHERE s.public_id=?",
            (public_id,),
        )

    @staticmethod
    def session_is_mutable(row: dict[str, object]) -> bool:
        if row["status"] not in {"created", "uploading", "validating", "ready"}:
            return False
        try:
            return datetime.fromisoformat(str(row["expires_at"])).astimezone(
                UTC
            ) > datetime.now(UTC)
        except ValueError:
            return False

    @staticmethod
    def session_is_readable(row: dict[str, object]) -> bool:
        if row["status"] in {"consumed", "cancelled", "expired"}:
            return False
        try:
            return datetime.fromisoformat(str(row["expires_at"])).astimezone(
                UTC
            ) > datetime.now(UTC)
        except ValueError:
            return False

    def rotate_session_secret(
        self, public_id: str, secret_hmac: str
    ) -> tuple[int, int]:
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE upload_sessions SET secret_hmac=?,"
                "secret_generation=secret_generation+1,revision=revision+1,"
                "updated_at=? WHERE public_id=? AND status NOT IN "
                "('consumed','cancelled','expired')",
                (secret_hmac, datetime.now(UTC).isoformat(), public_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("upload resource unavailable")
            row = connection.execute(
                "SELECT secret_generation,revision FROM upload_sessions "
                "WHERE public_id=?",
                (public_id,),
            ).fetchone()
            return int(row["secret_generation"]), int(row["revision"])

    def create_file(
        self,
        *,
        session_id: str,
        public_id: str,
        path: str,
        name: str,
        person: str | None,
        size: int,
        digest: str,
        mime: str | None,
        chunk_size: int,
        max_files: int,
        max_reserved_bytes: int,
        replacement_for: str | None = None,
    ) -> None:
        with self._factory.transaction() as connection:
            session = connection.execute(
                "SELECT id,status,expires_at,input_type,archive_mode "
                "FROM upload_sessions "
                "WHERE public_id=?",
                (session_id,),
            ).fetchone()
            if session is None or not self.session_is_mutable(dict(session)):
                raise DatabaseConflict("upload resource unavailable")
            # Deleted declarations remain auditable but cannot permit unbounded
            # metadata churn within one session.
            count = connection.execute(
                "SELECT COUNT(*) FROM upload_files WHERE session_id=?", (session["id"],)
            ).fetchone()[0]
            if count >= max_files:
                raise DatabaseConflict("upload file limit reached")
            if (
                session["input_type"] == "merged_transcription"
                and replacement_for is None
                and connection.execute(
                    "SELECT 1 FROM upload_files WHERE session_id=? AND active=1 "
                    "AND status NOT IN ('deleted','replaced') LIMIT 1",
                    (session["id"],),
                ).fetchone()
                is not None
            ):
                raise DatabaseConflict("merged transcription session accepts one file")
            if (
                session["archive_mode"]
                and replacement_for is None
                and connection.execute(
                    "SELECT 1 FROM upload_files WHERE session_id=? AND active=1 "
                    "AND archive_parent_id IS NULL AND status NOT IN "
                    "('deleted','replaced') LIMIT 1",
                    (session["id"],),
                ).fetchone()
                is not None
            ):
                raise DatabaseConflict("archive session accepts one ZIP")
            reserved = connection.execute(
                "SELECT COALESCE(SUM(reserved_bytes),0) FROM upload_sessions "
                "WHERE status NOT IN ('consumed','cancelled','expired') "
                "AND julianday(expires_at)>julianday(?)",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()[0]
            if size > max_reserved_bytes or reserved + size > max_reserved_bytes:
                raise DatabaseConflict("upload capacity unavailable")
            replacement_id = None
            if replacement_for is not None:
                replacement = connection.execute(
                    "SELECT id FROM upload_files WHERE public_id=? AND session_id=? "
                    "AND status IN ('ready','invalid') AND active=1",
                    (replacement_for, session["id"]),
                ).fetchone()
                if replacement is None:
                    raise DatabaseConflict("replacement resource unavailable")
                replacement_id = replacement["id"]
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT INTO upload_files("
                "public_id,session_id,status,storage_path,original_filename,"
                "display_name,person,declared_mime,declared_bytes,sha256_hex,"
                "chunk_size,active,replacement_for_id,created_at,updated_at) "
                "VALUES (?,?,'created',?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    public_id,
                    session["id"],
                    path,
                    name,
                    name,
                    person,
                    mime,
                    size,
                    digest,
                    chunk_size,
                    0 if replacement_id is not None else 1,
                    replacement_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE upload_sessions SET status='uploading',"
                "reserved_bytes=reserved_bytes+?,revision=revision+1,"
                "updated_at=?,last_activity_at=? WHERE id=?",
                (size, now, now, session["id"]),
            )

    def files_for_session(
        self, session_id: str, *, limit: int
    ) -> list[dict[str, object]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT f.* FROM upload_files f JOIN upload_sessions s "
                "ON s.id=f.session_id WHERE s.public_id=? AND f.status NOT IN "
                "('deleted','replaced') ORDER BY f.active DESC,f.id LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def maintenance_files(
        self, *, limit: int, after_id: int = 0
    ) -> list[dict[str, object]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT f.*,s.status AS session_status FROM upload_files f JOIN "
                "upload_sessions s ON s.id=f.session_id WHERE f.id>? AND "
                "(f.status IN ('deleted','replaced') OR s.status IN "
                "('cancelled','expired')) AND f.storage_cleaned_at IS NULL "
                "ORDER BY f.id LIMIT ?",
                (after_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def startup_nonterminal_files(
        self, *, after_id: int, limit: int
    ) -> list[dict[str, object]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT f.*,s.status AS session_status FROM upload_files f JOIN "
                "upload_sessions s ON s.id=f.session_id WHERE f.id>? AND "
                "f.status IN ('created','uploading','finalizing','verifying') "
                "AND s.status IN ('created','uploading','validating','ready') "
                "AND julianday(s.expires_at)>julianday(?) "
                "ORDER BY f.id LIMIT ?",
                (after_id, datetime.now(UTC).isoformat(), limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def mark_storage_cleaned(self, file_id: str) -> None:
        with self._factory.transaction() as connection:
            connection.execute(
                "UPDATE upload_files SET storage_cleaned_at=? WHERE public_id=? "
                "AND storage_cleaned_at IS NULL",
                (datetime.now(UTC).isoformat(), file_id),
            )

    def mark_integrity_invalid(self, file_id: str) -> None:
        with self._factory.transaction() as connection:
            now = datetime.now(UTC).isoformat()
            row = connection.execute(
                "SELECT session_id FROM upload_files WHERE public_id=? AND status IN "
                "('created','uploading','finalizing','verifying')",
                (file_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                "UPDATE upload_files SET status='invalid',validation_error_code="
                "'upload_integrity_failed',revision=revision+1,updated_at=? "
                "WHERE public_id=? AND status IN "
                "('created','uploading','finalizing','verifying')",
                (now, file_id),
            )
            connection.execute(
                "UPDATE upload_validations SET status='cancel_requested' WHERE "
                "file_id=(SELECT id FROM upload_files WHERE public_id=?) AND "
                "status IN ('queued','running')",
                (file_id,),
            )
            self._refresh_session_status(connection, row["session_id"])

    def expire_sessions(self, *, limit: int) -> int:
        with self._factory.transaction() as connection:
            now = datetime.now(UTC).isoformat()
            sessions = connection.execute(
                "SELECT id FROM upload_sessions WHERE "
                "status NOT IN ('consumed','expired') "
                "AND julianday(expires_at)<=julianday(?) ORDER BY id LIMIT ?",
                (now, limit),
            ).fetchall()
            ids = [row["id"] for row in sessions]
            if not ids:
                return 0
            marks = ",".join("?" for _ in ids)
            connection.execute(
                f"UPDATE upload_sessions SET status='expired',reserved_bytes=0,"
                f"revision=revision+1,updated_at=? WHERE id IN ({marks})",
                (now, *ids),
            )
            connection.execute(
                f"UPDATE upload_validations SET status='cancel_requested' WHERE "
                f"status IN ('queued','running') AND file_id IN (SELECT id FROM "
                f"upload_files WHERE session_id IN ({marks}))",
                ids,
            )
            return len(ids)

    def file_for_session(
        self, session_id: str, file_id: str
    ) -> dict[str, object] | None:
        return self._one(
            "SELECT f.* FROM upload_files f JOIN upload_sessions s "
            "ON s.id=f.session_id WHERE s.public_id=? AND f.public_id=?",
            (session_id, file_id),
        )

    def advance(self, file_id: str, offset: int, content_size: int, digest: str) -> int:
        with self._factory.transaction() as connection:
            row = connection.execute(
                "SELECT f.id,f.declared_bytes FROM upload_files f "
                "JOIN upload_sessions s "
                "ON s.id=f.session_id WHERE f.public_id=? AND f.confirmed_offset=? "
                "AND f.status IN ('created','uploading') "
                "AND s.status IN ('created','uploading','validating','ready') "
                "AND julianday(s.expires_at) > julianday(?)",
                (file_id, offset, datetime.now(UTC).isoformat()),
            ).fetchone()
            if row is None or offset + content_size > row["declared_bytes"]:
                raise DatabaseConflict("upload offset conflict")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT INTO upload_chunks("
                "file_id,offset_bytes,byte_count,sha256_hex,created_at) "
                "VALUES (?,?,?,?,?)",
                (row["id"], offset, content_size, digest, now),
            )
            cursor = connection.execute(
                "UPDATE upload_files SET status='uploading',confirmed_offset=?,"
                "revision=revision+1,updated_at=? WHERE id=? AND confirmed_offset=?",
                (offset + content_size, now, row["id"], offset),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("upload offset conflict")
            return offset + content_size

    def chunk_matches(
        self, file_id: str, offset: int, content_size: int, digest: str
    ) -> bool:
        row = self._one(
            "SELECT 1 FROM upload_chunks c JOIN upload_files f ON f.id=c.file_id "
            "WHERE f.public_id=? AND c.offset_bytes=? AND c.byte_count=? "
            "AND c.sha256_hex=?",
            (file_id, offset, content_size, digest),
        )
        return row is not None

    def set_person(
        self, session_id: str, file_id: str, expected_revision: int, person: str
    ) -> int:
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE upload_files SET person=?,revision=revision+1,updated_at=? "
                "WHERE public_id=? AND session_id=(SELECT id FROM upload_sessions "
                "WHERE public_id=?) AND revision=? "
                "AND status NOT IN ('replaced','deleted') AND "
                "(SELECT input_type='audio' FROM upload_sessions "
                "WHERE public_id=?) AND "
                "(SELECT status NOT IN ('consumed','cancelled','expired') "
                "AND julianday(expires_at)>julianday(?) "
                "FROM upload_sessions WHERE public_id=?)",
                (
                    person,
                    datetime.now(UTC).isoformat(),
                    file_id,
                    session_id,
                    expected_revision,
                    session_id,
                    datetime.now(UTC).isoformat(),
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("resource revision conflict")
            return expected_revision + 1

    def queue_validation(
        self, session_id: str, file_id: str, expected_revision: int, validation_id: str
    ) -> None:
        with self._factory.transaction() as connection:
            row = connection.execute(
                "SELECT f.id,f.validation_attempt FROM upload_files f "
                "JOIN upload_sessions s ON s.id=f.session_id "
                "WHERE s.public_id=? AND f.public_id=? AND f.revision=? "
                "AND f.status IN ('created','uploading','invalid') "
                "AND s.status IN ('created','uploading','validating','ready') "
                "AND julianday(s.expires_at)>julianday(?) "
                "AND f.confirmed_offset=f.declared_bytes",
                (session_id, file_id, expected_revision, datetime.now(UTC).isoformat()),
            ).fetchone()
            if row is None:
                raise DatabaseConflict("resource revision conflict")
            attempt = int(row["validation_attempt"]) + 1
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT INTO upload_validations("
                "public_id,file_id,status,attempt,queued_at) "
                "VALUES (?,?,'queued',?,?)",
                (validation_id, row["id"], attempt, now),
            )
            connection.execute(
                "UPDATE upload_files SET status='finalizing',validation_attempt=?,"
                "revision=revision+1,updated_at=? WHERE id=?",
                (attempt, now, row["id"]),
            )
            connection.execute(
                "UPDATE upload_sessions SET status='validating',revision=revision+1,"
                "updated_at=?,last_activity_at=? WHERE id=(SELECT session_id FROM "
                "upload_files WHERE id=?) "
                "AND status NOT IN ('consumed','cancelled','expired')",
                (now, now, row["id"]),
            )

    def cancel_session(self, session_id: str, expected_revision: int) -> int:
        with self._factory.transaction() as connection:
            now = datetime.now(UTC).isoformat()
            cursor = connection.execute(
                "UPDATE upload_sessions SET status='cancelled',cancelled_at=?,"
                "reserved_bytes=0,revision=revision+1,updated_at=? "
                "WHERE public_id=? AND revision=? "
                "AND status NOT IN ('consumed','cancelled','expired') "
                "AND julianday(expires_at)>julianday(?)",
                (now, now, session_id, expected_revision, now),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("resource revision conflict")
            connection.execute(
                "UPDATE upload_validations SET status='cancel_requested' "
                "WHERE status IN ('queued','running') AND file_id IN "
                "(SELECT id FROM upload_files WHERE session_id="
                "(SELECT id FROM upload_sessions WHERE public_id=?))",
                (session_id,),
            )
            return expected_revision + 1

    def delete_file(self, session_id: str, file_id: str, expected_revision: int) -> int:
        with self._factory.transaction() as connection:
            row = connection.execute(
                "SELECT f.id,f.declared_bytes,f.session_id FROM upload_files f "
                "JOIN upload_sessions s ON s.id=f.session_id WHERE f.public_id=? "
                "AND s.public_id=? AND f.revision=? AND f.status!='deleted' "
                "AND s.status NOT IN ('consumed','cancelled','expired') "
                "AND julianday(s.expires_at)>julianday(?)",
                (file_id, session_id, expected_revision, datetime.now(UTC).isoformat()),
            ).fetchone()
            if row is None:
                raise DatabaseConflict("resource revision conflict")
            cursor = connection.execute(
                "UPDATE upload_files SET status='deleted',active=0,"
                "revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (datetime.now(UTC).isoformat(), row["id"], expected_revision),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("resource revision conflict")
            connection.execute(
                "UPDATE upload_validations SET status='cancel_requested' "
                "WHERE file_id=? AND status IN ('queued','running')",
                (row["id"],),
            )
            connection.execute(
                "UPDATE upload_sessions SET reserved_bytes="
                "MAX(0,reserved_bytes-?),updated_at=? WHERE id=?",
                (
                    row["declared_bytes"],
                    datetime.now(UTC).isoformat(),
                    row["session_id"],
                ),
            )
            self._refresh_session_status(connection, row["session_id"])
            return expected_revision + 1

    def queued_validations(self, *, limit: int) -> list[dict[str, object]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT v.id AS validation_row_id,v.public_id AS validation_id,"
                "v.attempt,f.*,s.public_id AS session_public_id,"
                "s.input_type AS session_input_type,"
                "s.archive_mode AS session_archive_mode FROM "
                "upload_validations v JOIN upload_files f ON f.id=v.file_id "
                "JOIN upload_sessions s ON s.id=f.session_id WHERE "
                "v.status='queued' ORDER BY v.queued_at,v.id LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def requeue_interrupted_validations(self) -> int:
        with self._factory.transaction() as connection:
            now = datetime.now(UTC).isoformat()
            rows = connection.execute(
                "SELECT v.file_id FROM upload_validations v JOIN upload_files f "
                "ON f.id=v.file_id JOIN upload_sessions s ON s.id=f.session_id "
                "WHERE v.status='running' AND f.status='verifying' AND "
                "s.status NOT IN ('consumed','cancelled','expired') AND "
                "julianday(s.expires_at)>julianday(?)",
                (now,),
            ).fetchall()
            connection.execute(
                "UPDATE upload_validations SET status='cancel_requested' "
                "WHERE status='running'"
            )
            for row in rows:
                connection.execute(
                    "UPDATE upload_validations SET status='queued',started_at=NULL "
                    "WHERE file_id=? AND status='cancel_requested'",
                    (row["file_id"],),
                )
                connection.execute(
                    "UPDATE upload_files SET status='finalizing',updated_at=? "
                    "WHERE id=? AND status='verifying'",
                    (now, row["file_id"]),
                )
            return len(rows)

    def claim_validation(self, validation_id: str) -> bool:
        with self._factory.transaction() as connection:
            now = datetime.now(UTC).isoformat()
            cursor = connection.execute(
                "UPDATE upload_validations SET status='running',started_at=? WHERE "
                "public_id=? AND status='queued' AND file_id IN (SELECT f.id FROM "
                "upload_files f JOIN upload_sessions s ON s.id=f.session_id WHERE "
                "f.status='finalizing' "
                "AND s.status NOT IN ('consumed','cancelled','expired') "
                "AND julianday(s.expires_at)>julianday(?))",
                (now, validation_id, now),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "UPDATE upload_files SET status='verifying',updated_at=? WHERE id="
                "(SELECT file_id FROM upload_validations WHERE public_id=?)",
                (now, validation_id),
            )
            return True

    def validation_is_running(self, validation_id: str) -> bool:
        return self._one(
            "SELECT 1 FROM upload_validations WHERE public_id=? AND status='running'",
            (validation_id,),
        ) is not None

    def set_archive_phase(self, validation_id: str, phase: str) -> bool:
        if phase not in {"extraction", "track_validation", "launch_preparation"}:
            raise ValueError("archive phase is invalid")
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE upload_sessions SET archive_phase=?,updated_at=? WHERE id=("
                "SELECT f.session_id FROM upload_validations v JOIN upload_files f "
                "ON f.id=v.file_id WHERE v.public_id=? AND v.status='running') "
                "AND archive_mode=1 AND status='validating'",
                (phase, datetime.now(UTC).isoformat(), validation_id),
            )
            return cursor.rowcount == 1

    def finish_validation(
        self,
        validation_id: str,
        *,
        detected_type: str | None,
        duration_ms: int | None,
        warning_code: str | None,
        error_code: str | None,
        schema_name: str | None = None,
        schema_version: str | None = None,
        token_count: int | None = None,
        error_path: str | None = None,
    ) -> None:
        with self._factory.transaction() as connection:
            row = connection.execute(
                "SELECT v.file_id,f.replacement_for_id FROM upload_validations v "
                "JOIN upload_files f ON f.id=v.file_id WHERE v.public_id=? "
                "AND v.status='running'",
                (validation_id,),
            ).fetchone()
            if row is None:
                return
            now = datetime.now(UTC).isoformat()
            status = "failed" if error_code else "completed"
            connection.execute(
                "UPDATE upload_validations SET status=?,error_code=?,warning_code=?,"
                "finished_at=? WHERE public_id=? AND status='running'",
                (status, error_code, warning_code, now, validation_id),
            )
            updated = connection.execute(
                "UPDATE upload_files SET status=?,detected_type=?,duration_ms=?,"
                "validation_error_code=?,validation_warning_code=?,schema_name=?,"
                "schema_version=?,token_count=?,validation_error_path=?,updated_at=?,"
                "revision=revision+1 WHERE id=? AND status='verifying'",
                (
                    "invalid" if error_code else "ready",
                    detected_type,
                    duration_ms,
                    error_code,
                    warning_code,
                    schema_name,
                    schema_version,
                    token_count,
                    error_path,
                    now,
                    row["file_id"],
                ),
            )
            if updated.rowcount != 1:
                return
            if not error_code and row["replacement_for_id"] is not None:
                connection.execute(
                    "UPDATE upload_files SET active=1 WHERE id=? AND status='ready'",
                    (row["file_id"],),
                )
                connection.execute(
                    "UPDATE upload_files SET active=0,status='replaced',updated_at=? "
                    "WHERE id=? AND active=1",
                    (now, row["replacement_for_id"]),
                )
            self._refresh_session_status(
                connection, row["file_id"], file_id_is_row_id=True
            )

    def finish_archive_validation(
        self,
        validation_id: str,
        tracks: tuple[dict[str, object], ...],
        *,
        excluded_count: int,
    ) -> bool:
        """Atomically replace one validated ZIP with its extracted track rows."""
        if not tracks or excluded_count < 0:
            raise DatabaseConflict("archive validation result is invalid")
        with self._factory.transaction() as connection:
            source = connection.execute(
                "SELECT v.file_id,f.session_id,f.declared_bytes FROM "
                "upload_validations v JOIN upload_files f ON f.id=v.file_id "
                "JOIN upload_sessions s ON s.id=f.session_id WHERE v.public_id=? "
                "AND v.status='running' AND f.status='verifying' "
                "AND s.archive_mode=1",
                (validation_id,),
            ).fetchone()
            if source is None:
                return False
            now = datetime.now(UTC).isoformat()
            for track in tracks:
                status = str(track["status"])
                if status not in {"ready", "invalid"}:
                    raise DatabaseConflict("archive track status is invalid")
                connection.execute(
                    "INSERT INTO upload_files(public_id,session_id,status,"
                    "storage_path,original_filename,display_name,person,"
                    "declared_mime,declared_bytes,confirmed_offset,sha256_hex,"
                    "detected_type,duration_ms,validation_error_code,"
                    "validation_warning_code,active,archive_parent_id,"
                    "archive_entry_name,created_at,updated_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        track["public_id"],
                        source["session_id"],
                        status,
                        track["storage_path"],
                        track["display_name"],
                        track["display_name"],
                        track["person"],
                        track["mime"],
                        track["size"],
                        track["size"],
                        track["sha256"],
                        track["detected_type"],
                        track.get("duration_ms"),
                        track.get("error_code"),
                        track.get("warning_code"),
                        1,
                        source["file_id"],
                        track["archive_name"],
                        now,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE upload_validations SET status='completed',finished_at=? "
                "WHERE public_id=? AND status='running'",
                (now, validation_id),
            )
            connection.execute(
                "UPDATE upload_files SET status='deleted',active=0,"
                "archive_excluded_count=?,revision=revision+1,updated_at=? "
                "WHERE id=? AND status='verifying'",
                (excluded_count, now, source["file_id"]),
            )
            total = sum(int(track["size"]) for track in tracks)
            connection.execute(
                "UPDATE upload_sessions SET reserved_bytes=?,updated_at=? "
                "WHERE id=?",
                (total, now, source["session_id"]),
            )
            self._refresh_session_status(connection, source["session_id"])
            return True

    @staticmethod
    def _refresh_session_status(
        connection: object, identifier: object, *, file_id_is_row_id: bool = False
    ) -> None:
        """Recompute the pre-job aggregate state from active upload files."""
        if file_id_is_row_id:
            session = connection.execute(
                "SELECT session_id FROM upload_files WHERE id=?", (identifier,)
            ).fetchone()
            if session is None:
                return
            session_id = session["session_id"]
        else:
            session_id = identifier
        rows = connection.execute(
            "SELECT status FROM upload_files WHERE session_id=? AND active=1 "
            "AND status!='deleted'",
            (session_id,),
        ).fetchall()
        if rows and all(row["status"] == "ready" for row in rows):
            status = "ready"
        elif any(row["status"] in {"finalizing", "verifying"} for row in rows):
            status = "validating"
        else:
            status = "uploading"
        now = datetime.now(UTC).isoformat()
        connection.execute(
            "UPDATE upload_sessions SET status=?,revision=revision+1,updated_at=? "
            "WHERE id=? AND status NOT IN ('consumed','cancelled','expired')",
            (status, now, session_id),
        )

    def _one(self, sql: str, values: tuple[object, ...]) -> dict[str, object] | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(sql, values).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()
