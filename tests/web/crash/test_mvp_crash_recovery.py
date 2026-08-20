"""Crash-window acceptance tests for durable web state transitions."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.db.repositories.jobs import JobRepository
from tara_web.domain.models import Job
from tara_web.orchestration.job_service import JobService
from tara_web.services.chunk_upload import ChunkUploadService
from tara_web.services.idempotency import SecretHmac
from tara_web.services.upload_maintenance import drain_startup_uploads
from tara_web.services.upload_sessions import UploadSessionService
from tara_web.storage.artifacts import validate_final_yaml_v1
from tara_web.storage.layout import StorageLayout
from tara_web.storage.reconciliation import reconcile_all


class _MergedWorkspace:
    def prepare_merged_request_paths(
        self, _job_id: int, _public_id: str
    ) -> tuple[str, None, None]:
        return ("inputs/" + "a" * 32 + ".yaml", None, None)

    def recover(self) -> list[int]:
        return []


def _stack(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    connection = factory.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    repository = AudioUploadRepository(factory)
    layout = StorageLayout(root)
    sessions = UploadSessionService(
        repository,
        layout,
        SecretHmac(b"m" * 32),
        max_files=20,
        max_sessions=40,
        max_reserved_bytes=1_000_000,
        max_upload_bytes=1_000_000,
    )
    chunks = ChunkUploadService(sessions, repository, layout, max_chunk_bytes=1_000_000)
    return factory, repository, layout, sessions, chunks


def _seed_ready(factory: ConnectionFactory, index: int = 0) -> tuple[str, str, str]:
    session_id = f"session_{index:016d}"
    file_id = f"file_{index:016d}"
    secret = "v1:" + "a" * 64
    now = datetime.now(UTC).isoformat()
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with factory.transaction() as connection:
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,revision,"
            "reserved_bytes,expires_at,created_at,updated_at,input_type) "
            "VALUES(?,?,'ready',1,1,?,?,?,'merged_transcription')",
            (session_id, secret, expires, now, now),
        )
        connection.execute(
            "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
            "declared_bytes,confirmed_offset,sha256_hex,active,created_at,updated_at) "
            "SELECT ?,id,'ready',?,1,1,?,1,?,? FROM upload_sessions WHERE public_id=?",
            (file_id, f"uploads/{session_id}/{file_id}.part", "0" * 64, now, now, session_id),
        )
    return session_id, file_id, secret


def _promote(factory: ConnectionFactory, index: int = 0) -> str:
    session_id, _, secret = _seed_ready(factory, index)
    job_id = f"job_{index:016d}"
    JobRepository(factory).promote(
        Job(
            job_id,
            session_id,
            secret,
            "crash-test",
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            job_type="merged_transcription",
        ),
        expected_session_revision=1,
        max_waiting_jobs=25,
    )
    return job_id


@pytest.mark.parametrize(
    "window",
    [
        "reservation",
        "chunk",
        "finalization",
        "promotion",
        "claim",
        "progress",
        "result_write",
        "delete",
    ],
)
def test_restart_converges_each_durable_crash_window(tmp_path: Path, window: str) -> None:
    factory, uploads, layout, sessions, chunks = _stack(tmp_path)

    if window in {"reservation", "chunk", "finalization"}:
        content = b"ID3-crash-window"
        created = sessions.create()
        declared = sessions.declare_file(
            created.session_id,
            created.secret,
            filename="Alice.mp3",
            size=len(content),
            sha256_hex=hashlib.sha256(content).hexdigest(),
            mime="audio/mpeg",
        )
        file_id = str(declared["file_id"])
        row = uploads.file_for_session(created.session_id, file_id)
        assert row is not None
        target = layout.upload_path(str(row["storage_path"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        if window == "reservation":
            target.write_bytes(b"")
        else:
            chunks.write(
                created.session_id,
                file_id,
                created.secret,
                offset=0,
                digest=hashlib.sha256(content).hexdigest(),
                content=content,
            )
            if window == "chunk":
                target.write_bytes(content + b"uncommitted-suffix")
            else:
                revision = int(uploads.file_for_session(created.session_id, file_id)["revision"])
                uploads.queue_validation(
                    created.session_id,
                    file_id,
                    revision,
                    "validation_0000000000000000",
                )
                validation = uploads.queued_validations(limit=1)[0]
                assert uploads.claim_validation(str(validation["validation_id"]))
                assert uploads.requeue_interrupted_validations() == 1

        drain_startup_uploads(uploads, layout, batch_size=2)
        drain_startup_uploads(uploads, layout, batch_size=2)
        current = uploads.file_for_session(created.session_id, file_id)
        assert current is not None
        assert target.stat().st_size == int(current["confirmed_offset"])
        if window == "finalization":
            assert current["status"] == "finalizing"
            assert uploads.queued_validations(limit=1)

    elif window == "promotion":
        session_id, _, secret = _seed_ready(factory)
        candidate = Job(
            "job_0000000000000000",
            session_id,
            secret,
            "crash-test",
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            job_type="merged_transcription",
        )
        with pytest.raises(DatabaseConflict):
            JobRepository(factory).promote(candidate, expected_session_revision=999)
        JobRepository(factory).promote(candidate, expected_session_revision=1)
        database = factory.connect()
        try:
            assert database.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
            assert database.execute("SELECT status FROM upload_sessions").fetchone()[0] == "consumed"
        finally:
            database.close()

    elif window in {"claim", "progress"}:
        job_id = _promote(factory)
        service = JobService(factory, workspace_service=_MergedWorkspace())
        assert service.claim_next(max_active_jobs=5) is not None
        if window == "progress":
            with factory.transaction() as connection:
                connection.execute(
                    "UPDATE jobs SET stage='transcription',stage_progress_milli=500,"
                    "total_progress_milli=250 WHERE public_id=?",
                    (job_id,),
                )
        assert service.recover() == []
        assert service.recover() == []
        database = factory.connect()
        try:
            row = database.execute(
                "SELECT status,error_code FROM jobs WHERE public_id=?", (job_id,)
            ).fetchone()
            assert tuple(row) == ("failed", "server_interrupted")
        finally:
            database.close()

    else:
        job_id = _promote(factory)
        repository = ArtifactRepository(factory)
        destination = layout.new_artifact(job_id, "final_yaml")
        content = b"schema_version: 1\nsummary:\n  title: crash recovery\n"
        artifact_id = repository.create_pending(
            job_id=1,
            artifact_type="final_yaml",
            retention_kind="final_result",
            relative_path=destination.relative_path,
            original_filename=None,
            max_per_job=10,
        )
        target = layout.artifact_path(destination.relative_path)
        target.write_bytes(content)
        if os.name != "nt":
            target.chmod(0o600)
        repository.stage(
            artifact_id,
            byte_size=len(content),
            sha256_hex=hashlib.sha256(content).hexdigest(),
            temporary_name=f".{destination.filename}.{'a' * 32}.tmp",
        )
        reconcile_all(
            layout,
            repository,
            limit=2,
            max_bytes=1_000_000,
            validator=validate_final_yaml_v1,
        )
        if window == "delete":
            repository.begin_delete_attempt(artifact_id)
            reconcile_all(
                layout,
                repository,
                limit=2,
                max_bytes=1_000_000,
                validator=validate_final_yaml_v1,
            )
            assert not target.exists()
        database = factory.connect()
        try:
            expected = "deleted" if window == "delete" else "ready"
            assert database.execute(
                "SELECT storage_state FROM job_artifacts WHERE id=?", (artifact_id,)
            ).fetchone()[0] == expected
            assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            database.close()
