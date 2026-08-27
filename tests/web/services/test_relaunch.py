from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.services.relaunch import RelaunchService
from tara_web.storage.layout import StorageLayout


def _seed_terminal_job(tmp_path: Path) -> tuple[ConnectionFactory, RelaunchService]:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    now = datetime.now(UTC)
    connection = factory.connect()
    try:
        migrate(connection)
        session_id = connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at,language,context_text,previous_summaries_text) "
            "VALUES (?,?, 'consumed',?,?,?,'fr','private context','private summary') "
            "RETURNING id",
            (
                "session-000000000001",
                "v1:" + "a" * 64,
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,expires_at,created_at,updated_at,finished_at,"
            "input_file_count,revision,job_type,language) "
            "VALUES ('job-0000000000000001',?,?, 'timed_out','test',?,?,?,?,1,3,"
            "'audio','fr')",
            (
                session_id,
                "v1:" + "a" * 64,
                (now + timedelta(days=7)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return factory, RelaunchService(StorageLayout(root))


def test_identical_relaunch_is_disabled_even_before_cleanup(tmp_path: Path) -> None:
    factory, service = _seed_terminal_job(tmp_path)

    with factory.transaction() as connection, pytest.raises(
        DatabaseConflict, match="identical relaunch"
    ):
        service.create_identical(
            connection,
            source_public_id="job-0000000000000001",
            expected_revision=3,
            job_public_id="job-0000000000000002",
            session_public_id="session-000000000002",
        )


def test_editable_relaunch_is_empty_pending_and_single_use(tmp_path: Path) -> None:
    factory, service = _seed_terminal_job(tmp_path)

    with factory.transaction() as connection:
        result = service.create_editable(
            connection,
            source_public_id="job-0000000000000001",
            expected_revision=3,
            session_public_id="session-000000000002",
            secret_hmac="v1:" + "b" * 64,
            admission_identity_hmac="v1:" + "c" * 64,
            max_sessions=1,
            max_sessions_per_identity=1,
            max_pending_sessions=5,
            max_pending_sessions_per_identity=5,
            max_reserved_bytes=1,
            pending_ttl_seconds=1800,
        )
    assert result == {"session_id": "session-000000000002", "revision": 1}

    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT status,reserved_bytes,context_text,previous_summaries_text,"
            "relaunch_parent_job_id FROM upload_sessions WHERE public_id=?",
            ("session-000000000002",),
        ).fetchone()
        assert tuple(row[:4]) == ("created", 0, "", "")
        assert row[4] is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM upload_files WHERE session_id=(SELECT id FROM "
            "upload_sessions WHERE public_id='session-000000000002')"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    with factory.transaction() as connection, pytest.raises(
        DatabaseConflict, match="editable relaunch"
    ):
        service.create_editable(
            connection,
            source_public_id="job-0000000000000001",
            expected_revision=4,
            session_public_id="session-000000000003",
            secret_hmac="v1:" + "b" * 64,
            admission_identity_hmac="v1:" + "c" * 64,
            max_sessions=1,
            max_sessions_per_identity=1,
            max_pending_sessions=5,
            max_pending_sessions_per_identity=5,
            max_reserved_bytes=1,
            pending_ttl_seconds=1800,
        )


def test_editable_relaunch_obeys_pending_identity_quota(tmp_path: Path) -> None:
    factory, service = _seed_terminal_job(tmp_path)
    now = datetime.now(UTC).isoformat()
    with factory.transaction() as connection:
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at,admission_identity_hmac) "
            "VALUES ('session-capacity-blocker',?,'created',?,?,?,?)",
            (
                "v1:" + "b" * 64,
                (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                now,
                now,
                "v1:" + "c" * 64,
            ),
        )

    with factory.transaction() as connection, pytest.raises(
        DatabaseConflict, match="pending capacity"
    ):
        service.create_editable(
            connection,
            source_public_id="job-0000000000000001",
            expected_revision=3,
            session_public_id="session-000000000004",
            secret_hmac="v1:" + "b" * 64,
            admission_identity_hmac="v1:" + "c" * 64,
            max_sessions=100,
            max_sessions_per_identity=100,
            max_pending_sessions=100,
            max_pending_sessions_per_identity=1,
            max_reserved_bytes=10_000,
            pending_ttl_seconds=1800,
        )
