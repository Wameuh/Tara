from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.db.repositories.jobs import JobRepository
from tara_web.domain.models import Job


def test_concurrent_admission_stops_at_configured_capacity(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root, busy_timeout_ms=10_000)
    connection = database.connect()
    now = datetime.now(UTC).isoformat()
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    secret = "v1:" + "a" * 64
    try:
        migrate(connection)
        for index in range(26):
            session_id = f"session_{index:016d}"
            connection.execute(
                "INSERT INTO upload_sessions("
                "public_id,secret_hmac,status,reserved_bytes,expires_at,created_at,"
                "updated_at) VALUES(?,?,'ready',1,?,?,?)",
                (session_id, secret, expires, now, now),
            )
            connection.execute(
                "INSERT INTO upload_files("
                "public_id,session_id,status,storage_path,declared_bytes,created_at,"
                "updated_at) SELECT ? ,id,'ready',?,1,?,? FROM upload_sessions "
                "WHERE public_id=?",
                (
                    f"file_{index:016d}",
                    f"uploads/session_{index:016d}/file_{index:016d}.part",
                    now,
                    now,
                    session_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    outcomes: list[str] = []
    lock = threading.Lock()

    def admit(index: int) -> None:
        try:
            JobRepository(database).promote(
                Job(
                    f"job_{index:016d}",
                    f"session_{index:016d}",
                    secret,
                    "v1",
                    expires,
                ),
                expected_session_revision=1,
                max_waiting_jobs=25,
            )
            outcome = "accepted"
        except DatabaseConflict:
            outcome = "full"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=admit, args=(index,)) for index in range(26)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("accepted") == 25
    assert outcomes.count("full") == 1
    connection = database.connect()
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='queued'"
            ).fetchone()[0]
            == 25
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM upload_sessions WHERE status='consumed'"
            ).fetchone()[0]
            == 25
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM upload_sessions WHERE status='ready'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()

    with pytest.raises(ValueError, match="waiting job limit"):
        JobRepository(database).promote(
            Job(
                "job_invalid_limit_0001",
                "session_0000000000000000",
                secret,
                "v1",
                expires,
            ),
            expected_session_revision=1,
            max_waiting_jobs=True,
        )
