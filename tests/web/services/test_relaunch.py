from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.orchestration.job_workspace import JobWorkspaceService
from tara_web.services.relaunch import RelaunchService
from tara_web.storage.cleanup import cleanup_expired_inputs
from tara_web.storage.layout import StorageLayout


def test_identical_relaunch_creates_one_distinct_job_with_reusable_inputs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    connection = factory.connect()
    now = datetime.now(UTC)
    source_public_id = "job-0000000000000001"
    content = b"ID3-identical-relaunch"
    digest = hashlib.sha256(content).hexdigest()
    try:
        migrate(connection)
        session_id = connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at,language) VALUES (?,?, 'consumed',?,?,?,'fr') "
            "RETURNING id",
            (
                "session-000000000001",
                "v1:" + "a" * 64,
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        ).fetchone()[0]
        job_id = connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,expires_at,created_at,updated_at,finished_at,"
            "input_file_count,revision) VALUES (?,?,?,'timed_out','test',?,?,?,?,1,3) "
            "RETURNING id",
            (
                source_public_id,
                session_id,
                "v1:" + "a" * 64,
                (now + timedelta(days=7)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        ).fetchone()[0]
        file_id = connection.execute(
            "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
            "original_filename,declared_bytes,confirmed_offset,sha256_hex,"
            "created_at,updated_at,job_id) VALUES (?,?, 'ready',?,?,?, ?,?,?,?,?) "
            "RETURNING id",
            (
                "file-0000000000000001",
                session_id,
                "uploads/session-000000000001/file-0000000000000001.part",
                "source.mp3",
                len(content),
                len(content),
                digest,
                now.isoformat(),
                now.isoformat(),
                job_id,
            ),
        ).fetchone()[0]
        layout = StorageLayout(root)
        layout.create_job_layout(source_public_id)
        relative = f"jobs/{source_public_id}/inputs/{'1' * 32}.bin"
        path = root / relative
        path.write_bytes(content)
        path.chmod(0o600)
        connection.execute(
            "INSERT INTO job_input_preparations(job_id,upload_file_id,"
            "destination_path,expected_bytes,sha256_hex,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'moved',?,?)",
            (
                job_id,
                file_id,
                relative,
                len(content),
                digest,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    service = RelaunchService(StorageLayout(root))
    with factory.transaction() as transaction:
        result = service.create_identical(
            transaction,
            source_public_id=source_public_id,
            expected_revision=3,
            job_public_id="job-0000000000000002",
            session_public_id="session-000000000002",
        )
    assert result == {"accepted": True, "job_id": "job-0000000000000002"}

    database = factory.connect()
    try:
        new = database.execute(
            "SELECT id,status,parent_job_id,current_attempt_number FROM jobs "
            "WHERE public_id='job-0000000000000002'"
        ).fetchone()
        assert tuple(new)[1:] == ("queued", job_id, 1)
        assert database.execute(
            "SELECT identical_relaunch_job_id FROM jobs WHERE id=?", (job_id,)
        ).fetchone()[0] == new[0]
    finally:
        database.close()
    assert JobWorkspaceService(factory, StorageLayout(root)).prepare(
        int(new[0]), "job-0000000000000002"
    ) == "inputs/source-manifest.json"
    with factory.transaction() as transaction:
        editable = service.create_editable(
            transaction,
            source_public_id=source_public_id,
            expected_revision=4,
            session_public_id="session-000000000004",
            secret_hmac="v1:" + "a" * 64,
        )
    assert editable == {"session_id": "session-000000000004", "revision": 1}
    database = factory.connect()
    try:
        copied = database.execute(
            "SELECT s.status,f.storage_path FROM upload_sessions s "
            "JOIN upload_files f ON f.session_id=s.id WHERE s.public_id=?",
            ("session-000000000004",),
        ).fetchone()
        assert copied[0] == "ready"
        assert (root / copied[1]).read_bytes() == content
    finally:
        database.close()
    with factory.transaction() as transaction, pytest.raises(DatabaseConflict):
        service.create_identical(
            transaction,
            source_public_id=source_public_id,
            expected_revision=4,
            job_public_id="job-0000000000000003",
            session_public_id="session-000000000003",
        )
    with factory.transaction() as transaction:
        transaction.execute(
            "UPDATE upload_files SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(hours=25)).isoformat(), file_id),
        )
    assert cleanup_expired_inputs(
        StorageLayout(root), ArtifactRepository(factory), batch_size=10
    ) == 1
    assert not path.exists()
    assert (root / "jobs/job-0000000000000002/inputs").exists()
