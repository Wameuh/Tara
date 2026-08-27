from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.storage.artifacts import ArtifactPolicy, ArtifactService
from tara_web.storage.cleanup import (
    cleanup_terminal_private_batch,
    cleanup_terminal_private_data,
)
from tara_web.storage.layout import StorageLayout

PUBLIC_RESULT = b"""schema_name: tara.public_result
schema_version: 26.0.1
content:
  title: Result
  sections:
    - section_id: overview-result
      section_type: overview
      title: Result
      blocks:
        - type: paragraph
          text: Result.
"""
JOB_ID = "job_terminal_000001"
SESSION_ID = "session_terminal_01"
FILE_ID = "file_terminal_00001"


def _seed_terminal_job(
    tmp_path: Path, status: str
) -> tuple[ConnectionFactory, StorageLayout, int, Path]:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    layout = StorageLayout(root)
    now = datetime.now(UTC).isoformat()
    connection = database.connect()
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,"
            "reserved_bytes,context_text,previous_summaries_text,expires_at,"
            "created_at,updated_at) VALUES(?,?,'consumed',123,'private context',"
            "'private summary',?,?,?)",
            (SESSION_ID, "v1:" + "a" * 64, now, now, now),
        )
        upload = layout.upload_file(SESSION_ID, FILE_ID)
        upload_path = layout.upload_path(upload.relative_path)
        upload_path.write_bytes(b"source audio")
        connection.execute(
            "INSERT INTO upload_files(public_id,session_id,job_id,status,"
            "storage_path,declared_bytes,sha256_hex,active,created_at,updated_at) "
            "VALUES(?,1,NULL,'ready',?,?,?,1,?,?)",
            (
                FILE_ID,
                upload.relative_path,
                len(b"source audio"),
                hashlib.sha256(b"source audio").hexdigest(),
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,expires_at,created_at,updated_at,finished_at) "
            "VALUES(?,1,?,?, 'test',?,?,?,?)",
            (JOB_ID, "v1:" + "b" * 64, status, now, now, now, now),
        )
        connection.execute("UPDATE upload_files SET job_id=1 WHERE id=1")
        layout.create_job_layout(JOB_ID)
        prepared = layout.new_artifact(JOB_ID, "audio_input")
        prepared_path = layout.artifact_path(prepared.relative_path)
        prepared_path.write_bytes(b"prepared source")
        connection.execute(
            "INSERT INTO job_input_preparations(job_id,upload_file_id,"
            "destination_path,expected_bytes,sha256_hex,state,created_at,updated_at) "
            "VALUES(1,1,?,?,?,'moved',?,?)",
            (
                prepared.relative_path,
                len(b"prepared source"),
                hashlib.sha256(b"prepared source").hexdigest(),
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    work = root / "jobs" / JOB_ID / "work"
    (work / "nested" / "transcripts").mkdir(parents=True)
    (work / "nested" / "transcripts" / "raw.txt").write_text("secret")
    return database, layout, 1, upload_path


def _write_result(
    database: ConnectionFactory, layout: StorageLayout
) -> tuple[int, Path]:
    outcome = ArtifactService(
        layout,
        ArtifactRepository(database),
        ArtifactPolicy(max_bytes=1024, minimum_free_bytes=0),
    ).write(
        job_id=1,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[PUBLIC_RESULT],
        validator=lambda path: None,
    )
    row = ArtifactRepository(database).get_final_ready(
        artifact_id=outcome.artifact_id, job_id=1
    )
    return outcome.artifact_id, layout.artifact_path(str(row["relative_path"]))


def test_completed_cleanup_preserves_only_final_and_scrubs_private_data(
    tmp_path: Path,
) -> None:
    database, layout, _, upload_path = _seed_terminal_job(tmp_path, "completed")
    final_id, final_path = _write_result(database, layout)
    result_junk = final_path.parent / "partial.tmp"
    result_junk.write_bytes(b"partial")
    external = tmp_path / "external-secret"
    external.write_text("must remain")
    link = layout.root / "jobs" / JOB_ID / "work" / "outside-link"
    try:
        link.symlink_to(external)
    except OSError:
        pass

    repository = ArtifactRepository(database)
    assert cleanup_terminal_private_data(layout, repository, JOB_ID)
    assert cleanup_terminal_private_data(layout, repository, JOB_ID)

    assert not upload_path.parent.exists()
    assert not (layout.root / "jobs" / JOB_ID / "inputs").exists()
    assert not (layout.root / "jobs" / JOB_ID / "work").exists()
    assert final_path.read_bytes() == PUBLIC_RESULT
    assert not result_junk.exists()
    assert external.read_text() == "must remain"
    connection = database.connect()
    try:
        job = connection.execute(
            "SELECT private_artifacts_cleaned_at FROM jobs WHERE id=1"
        ).fetchone()
        session = connection.execute(
            "SELECT context_text,previous_summaries_text,reserved_bytes "
            "FROM upload_sessions WHERE id=1"
        ).fetchone()
        artifact = connection.execute(
            "SELECT storage_state FROM job_artifacts WHERE id=?", (final_id,)
        ).fetchone()
        assert job[0] is not None
        assert tuple(session) == ("", "", 0)
        assert artifact[0] == "ready"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_input_preparations"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT storage_cleaned_at FROM upload_files WHERE id=1"
            ).fetchone()[0]
            is not None
        )
    finally:
        connection.close()

    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET status='deleted',private_artifacts_cleaned_at=NULL "
            "WHERE id=1"
        )
    assert cleanup_terminal_private_data(layout, repository, JOB_ID)
    assert not final_path.parent.exists()


@pytest.mark.parametrize(
    "status", ["failed", "timed_out", "cancelled", "cancel_failed"]
)
def test_unsuccessful_cleanup_removes_partial_results(
    tmp_path: Path, status: str
) -> None:
    database, layout, _, _ = _seed_terminal_job(tmp_path, status)
    _, result_path = _write_result(database, layout)

    assert cleanup_terminal_private_data(layout, ArtifactRepository(database), JOB_ID)
    assert not result_path.parent.exists()
    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT storage_state,sha256_hex,byte_size FROM job_artifacts"
        ).fetchone()
        assert tuple(row) == ("deleted", None, None)
    finally:
        connection.close()


def test_terminal_cleanup_batch_is_bounded_and_ignores_active_jobs(
    tmp_path: Path,
) -> None:
    database, layout, _, _ = _seed_terminal_job(tmp_path, "failed")
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET status='running' WHERE id=1")
    assert (
        cleanup_terminal_private_batch(
            layout, ArtifactRepository(database), batch_size=1
        )
        == 0
    )
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET status='failed' WHERE id=1")
    assert (
        cleanup_terminal_private_batch(
            layout, ArtifactRepository(database), batch_size=1
        )
        == 1
    )


def test_status_change_during_cleanup_keeps_the_job_retryable(tmp_path: Path) -> None:
    database, layout, _, _ = _seed_terminal_job(tmp_path, "completed")
    final_id, final_path = _write_result(database, layout)
    repository = ArtifactRepository(database)
    target = repository.terminal_private_cleanup_target(JOB_ID)
    assert target is not None

    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET status='deleted' WHERE id=1")
    assert not repository.complete_terminal_private_cleanup(
        1, str(target["status"]), final_id
    )
    with database.transaction() as connection:
        marker = connection.execute(
            "SELECT private_artifacts_cleaned_at FROM jobs WHERE id=1"
        ).fetchone()[0]
    assert marker is None
    assert cleanup_terminal_private_data(layout, repository, JOB_ID)
    assert not final_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_cleanup_failure_remains_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, layout, _, _ = _seed_terminal_job(tmp_path, "failed")
    from tara_web.storage import cleanup

    original = cleanup._remove_tree
    failed_once = False

    def fail_once(path: Path) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("simulated deletion failure")
        original(path)

    monkeypatch.setattr(cleanup, "_remove_tree", fail_once)
    repository = ArtifactRepository(database)
    assert not cleanup_terminal_private_data(layout, repository, JOB_ID)
    connection = database.connect()
    try:
        assert (
            connection.execute(
                "SELECT private_artifacts_cleaned_at FROM jobs WHERE id=1"
            ).fetchone()[0]
            is None
        )
    finally:
        connection.close()
    assert cleanup_terminal_private_data(layout, repository, JOB_ID)
