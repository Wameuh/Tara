"""Crash-safe creation of a distinct, single-use identical relaunch."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from tara_web.db.connection import DatabaseConflict
from tara_web.domain.models import utc_now
from tara_web.storage.atomic import read_regular, write_staged
from tara_web.storage.layout import StorageLayout
from tara_web.storage.uploads import write_chunk

from .upload_sessions import new_opaque_id


class RelaunchService:
    def __init__(self, layout: StorageLayout) -> None:
        self._layout = layout

    def create_identical(
        self,
        connection: object,
        *,
        source_public_id: str,
        expected_revision: int,
        job_public_id: str,
        session_public_id: str,
        session_retention_hours: int = 24,
    ) -> dict[str, object]:
        source = connection.execute(
            "SELECT * FROM jobs WHERE public_id=? AND revision=? "
            "AND status='timed_out' AND identical_relaunch_job_id IS NULL",
            (source_public_id, expected_revision),
        ).fetchone()
        if source is None:
            raise DatabaseConflict("identical relaunch is unavailable")
        preparations = connection.execute(
            "SELECT p.*,f.id AS file_id FROM job_input_preparations p "
            "JOIN upload_files f ON f.id=p.upload_file_id "
            "WHERE p.job_id=? AND p.state='moved' AND f.status='ready' "
            "AND f.active=1 ORDER BY p.id",
            (source["id"],),
        ).fetchall()
        if not preparations or len(preparations) != int(source["input_file_count"]):
            raise DatabaseConflict("identical relaunch inputs are unavailable")
        copied: list[tuple[object, str]] = []
        for row in preparations:
            old = self._layout.parse_artifact_path(str(row["destination_path"]))
            size = int(row["expected_bytes"])
            content = read_regular(
                self._layout, old, expected_bytes=size, max_bytes=max(1, size)
            )
            if hashlib.sha256(content).hexdigest() != str(row["sha256_hex"]):
                raise DatabaseConflict("identical relaunch inputs are unavailable")
            artifact_type = (
                "merged_transcription_input"
                if old.filename.endswith(".yaml")
                else "audio_input"
            )
            destination = self._layout.new_artifact(job_public_id, artifact_type)
            write_staged(
                self._layout,
                destination,
                (content,),
                max_bytes=max(1, size),
                minimum_free_bytes=0,
                hash_content=True,
                stage=lambda _result: None,
            )
            copied.append((row, destination.relative_path))
        now = datetime.now(UTC)
        parent_session = connection.execute(
            "SELECT * FROM upload_sessions WHERE id=?", (source["upload_session_id"],)
        ).fetchone()
        if parent_session is None:
            raise DatabaseConflict("identical relaunch session is unavailable")
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,secret_generation,"
            "status,revision,reserved_bytes,expires_at,created_at,updated_at,consumed_at,"
            "last_activity_at,relaunch_parent_job_id,language,context_text,"
            "previous_summaries_text,input_type,archive_mode) "
            "VALUES (?,?,?,'consumed',2,0,?,?,?,?,?,?,?,?,?,?,0)",
            (
                session_public_id,
                source["secret_hmac"],
                source["secret_generation"],
                (now + timedelta(hours=session_retention_hours)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                source["id"],
                parent_session["language"],
                parent_session["context_text"],
                parent_session["previous_summaries_text"],
                source["job_type"],
            ),
        )
        new_session_id = connection.execute(
            "SELECT id FROM upload_sessions WHERE public_id=?", (session_public_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,"
            "secret_generation,"
            "status,pipeline_version,created_at,updated_at,job_type,root_job_id,"
            "parent_job_id,current_attempt_number,expires_at,stage,input_file_count,"
            "language) VALUES (?,?,?,?,'queued',?,?,?,?,?,?,1,?,'queued',?,?)",
            (
                job_public_id,
                new_session_id,
                source["secret_hmac"],
                source["secret_generation"],
                source["pipeline_version"],
                now.isoformat(),
                now.isoformat(),
                source["job_type"],
                source["root_job_id"] or source["id"],
                source["id"],
                (now + timedelta(days=7)).isoformat(),
                len(preparations),
                source["language"],
            ),
        )
        new_job_id = connection.execute(
            "SELECT id FROM jobs WHERE public_id=?", (job_public_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO job_attempts(job_id,attempt_number,status) "
            "VALUES (?,1,'queued')",
            (new_job_id,),
        )
        for row, destination in copied:
            connection.execute(
                "INSERT INTO job_input_preparations(job_id,upload_file_id,"
                "destination_path,expected_bytes,sha256_hex,state,created_at,"
                "updated_at) "
                "VALUES (?,?,?,?,?,'moved',?,?)",
                (
                    new_job_id,
                    row["file_id"],
                    destination,
                    row["expected_bytes"],
                    row["sha256_hex"],
                    utc_now(),
                    utc_now(),
                ),
            )
        file_ids = [int(row["file_id"]) for row in preparations]
        marks = ",".join("?" for _ in file_ids)
        connection.execute(
            f"UPDATE upload_files SET job_id=? WHERE id IN ({marks})",
            (new_job_id, *file_ids),
        )
        cursor = connection.execute(
            "UPDATE jobs SET identical_relaunch_job_id=?,revision=revision+1,"
            "updated_at=? WHERE id=? AND revision=? "
            "AND identical_relaunch_job_id IS NULL",
            (new_job_id, utc_now(), source["id"], expected_revision),
        )
        if cursor.rowcount != 1:
            raise DatabaseConflict("identical relaunch is unavailable")
        return {"accepted": True, "job_id": job_public_id}

    def create_editable(
        self,
        connection: object,
        *,
        source_public_id: str,
        expected_revision: int,
        session_public_id: str,
        secret_hmac: str,
        retention_hours: int = 24,
    ) -> dict[str, object]:
        """Copy still-live immutable inputs into a fresh editable session."""
        source = connection.execute(
            "SELECT j.*,s.language,s.context_text,s.previous_summaries_text "
            "FROM jobs j JOIN upload_sessions s ON s.id=j.upload_session_id "
            "WHERE j.public_id=? AND j.revision=? AND j.status IN "
            "('failed','timed_out','cancelled','cancel_failed') "
            "AND julianday(j.expires_at)>julianday('now')",
            (source_public_id, expected_revision),
        ).fetchone()
        if source is None:
            raise DatabaseConflict("editable relaunch is unavailable")
        rows = connection.execute(
            "SELECT p.destination_path,p.expected_bytes,p.sha256_hex,p.created_at,"
            "f.* FROM job_input_preparations p JOIN upload_files f "
            "ON f.id=p.upload_file_id WHERE p.job_id=? AND p.state='moved' "
            "AND f.status='ready' AND f.active=1 ORDER BY p.id",
            (source["id"],),
        ).fetchall()
        now = datetime.now(UTC)
        copies: list[tuple[object, str, str, bytes]] = []
        for row in rows:
            created = datetime.fromisoformat(str(row["created_at"]))
            if created + timedelta(hours=24) <= now:
                continue
            size = int(row["expected_bytes"])
            managed = self._layout.parse_artifact_path(str(row["destination_path"]))
            content = read_regular(
                self._layout, managed, expected_bytes=size, max_bytes=max(1, size)
            )
            if hashlib.sha256(content).hexdigest() != str(row["sha256_hex"]):
                continue
            file_public_id = new_opaque_id("file")
            upload = self._layout.upload_file(session_public_id, file_public_id)
            write_chunk(self._layout, upload, 0, content)
            copies.append((row, file_public_id, upload.relative_path, content))
        status = "ready" if copies else "created"
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at,last_activity_at,relaunch_parent_job_id,language,"
            "context_text,previous_summaries_text,input_type,reserved_bytes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_public_id,
                secret_hmac,
                status,
                (now + timedelta(hours=retention_hours)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                source["id"],
                source["language"],
                source["context_text"],
                source["previous_summaries_text"],
                source["job_type"],
                sum(len(item[3]) for item in copies),
            ),
        )
        session_id = connection.execute(
            "SELECT id FROM upload_sessions WHERE public_id=?", (session_public_id,)
        ).fetchone()[0]
        for row, file_public_id, path, content in copies:
            connection.execute(
                "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
                "original_filename,declared_bytes,confirmed_offset,sha256_hex,revision,"
                "created_at,updated_at,person,display_name,declared_mime,detected_type,"
                "duration_ms,chunk_size,validation_attempt,active,schema_name,"
                "schema_version,token_count) VALUES (?,?,'ready',?,?,?,?,?,1,"
                "?,?,?,?,?,?,?,?,0,1,?,?,?)",
                (
                    file_public_id,
                    session_id,
                    path,
                    row["original_filename"],
                    len(content),
                    len(content),
                    row["sha256_hex"],
                    now.isoformat(),
                    now.isoformat(),
                    row["person"],
                    row["display_name"],
                    row["declared_mime"],
                    row["detected_type"],
                    row["duration_ms"],
                    row["chunk_size"],
                    row["schema_name"],
                    row["schema_version"],
                    row["token_count"],
                ),
            )
        return {"session_id": session_public_id, "revision": 1}
