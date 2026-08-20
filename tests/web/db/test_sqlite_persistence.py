from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara.token_limits import count_tokens
from tara_web.db.connection import ConnectionFactory, DatabaseConflict, DatabaseError
from tara_web.db.migrations import MIGRATIONS, Migration, migrate, schema_version
from tara_web.db.repositories.jobs import JobRepository
from tara_web.db.repositories.uploads import UploadRepository
from tara_web.domain.models import (
    Job,
    JobMetricsRecord,
    UploadSession,
    utc_now,
    validate_relative_storage_path,
)
from tara_web.services.idempotency import (
    IdempotencyConflict,
    IdempotencyService,
    SecretHmac,
)

LATEST_VERSION = 16


def factory(tmp_path: Path) -> ConnectionFactory:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700, parents=True)
    return ConnectionFactory(root / "tara.sqlite3", root)


def initialized_factory(tmp_path: Path) -> ConnectionFactory:
    result = factory(tmp_path)
    connection = result.connect()
    try:
        assert migrate(connection) == LATEST_VERSION
    finally:
        connection.close()
    return result


def test_promotion_rolls_back_after_partial_write(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    uploads = UploadRepository(db)
    uploads.reserve(session(), max_active_jobs=1, max_reserved_bytes=1_000)
    uploads.compare_and_set_status("session-0000000001", 1, "ready")
    connection = db.connect()
    try:
        connection.execute(
            "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
            "declared_bytes,created_at,updated_at) "
            "VALUES (?,1,'ready','input',0,?,?)",
            ("file-0000000000001", utc_now(), utc_now()),
        )
        connection.execute(
            "CREATE TRIGGER abort_consume BEFORE UPDATE OF status ON "
            "upload_sessions WHEN NEW.status='consumed' "
            "BEGIN SELECT RAISE(ABORT,'abort'); END"
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("column", "limit"),
    (("context_text", 2_000), ("previous_summaries_text", 50_000)),
)
def test_promotion_rejects_session_text_overflow_atomically_and_accepts_exact_limit(
    tmp_path: Path, column: str, limit: int
) -> None:
    def prepare(text: str) -> ConnectionFactory:
        db = initialized_factory(tmp_path / str(len(text)))
        uploads = UploadRepository(db)
        uploads.reserve(session(), max_active_jobs=1, max_reserved_bytes=1_000)
        uploads.compare_and_set_status("session-0000000001", 1, "ready")
        with db.transaction() as connection:
            connection.execute(f"UPDATE upload_sessions SET {column}=?", (text,))
            connection.execute(
                "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
                "declared_bytes,created_at,updated_at) "
                "VALUES (?,1,'ready','input',0,?,?)",
                ("file-0000000000001", utc_now(), utc_now()),
            )
        return db

    exact = "x " * (limit - 1) + "x"
    assert count_tokens(exact) == limit
    rejected = prepare(exact + " x")
    with pytest.raises(DatabaseConflict):
        JobRepository(rejected).promote(
            Job(
                "job-0000000000001",
                "session-0000000001",
                "v1:" + "a" * 64,
                "v1",
                (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            ),
            expected_session_revision=2,
        )
    connection = rejected.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 0
        )
        assert tuple(
            connection.execute("SELECT status,revision FROM upload_sessions").fetchone()
        ) == ("ready", 2)
    finally:
        connection.close()

    accepted = prepare(exact)
    JobRepository(accepted).promote(
        Job(
            "job-0000000000001",
            "session-0000000001",
            "v1:" + "a" * 64,
            "v1",
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        ),
        expected_session_revision=2,
    )


def session(number: int = 1) -> UploadSession:
    return UploadSession(
        f"session-{number:010d}",
        "v1:" + "a" * 64,
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


def test_migration_creates_expected_schema_and_pragmas(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    connection = db.connect()
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "upload_sessions",
            "upload_files",
            "upload_chunks",
            "jobs",
            "job_attempts",
            "job_artifacts",
            "idempotency_keys",
            "provider_circuits",
            "provider_usage_attempts",
            "job_metrics",
            "job_failure_metrics",
            "pre_job_error_metrics",
            "schema_version",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 100
        assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
    finally:
        connection.close()


def test_migration_is_idempotent_and_rejects_future_version(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    connection = db.connect()
    try:
        assert migrate(connection) == LATEST_VERSION
        connection.execute("UPDATE schema_version SET version = 17 WHERE id = 1")
        connection.commit()
        with pytest.raises(DatabaseError, match="newer"):
            migrate(connection)
    finally:
        connection.close()


def test_migration_checks_integrity_backups_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tara_web.db import migrations

    db = factory(tmp_path)
    connection = db.connect()
    try:
        initial = (migrations.Migration(1, "0001_initial.sql"),)
        assert migrate(connection, registry=initial) == 1
        backups = tmp_path / "backups"
        bad = migrations.Migration(2, "injected.sql")
        monkeypatch.setattr(
            migrations,
            "_migration_sql",
            lambda _: "CREATE TABLE rollback_probe(id INTEGER); INVALID SQL;",
        )
        with pytest.raises(DatabaseError, match="migration failed"):
            migrate(
                connection,
                database_path=db.path,
                backups_root=backups,
                registry=(migrations.Migration(1, "0001_initial.sql"), bad),
            )
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'rollback_probe'"
        ).fetchone()
        assert schema_version(connection) == 1
        assert len(list(backups.glob("*.sqlite3"))) == 1
        monkeypatch.setattr(
            migrations,
            "_assert_integrity",
            lambda _: (_ for _ in ()).throw(
                DatabaseError("database integrity check failed")
            ),
        )
        with pytest.raises(DatabaseError, match="integrity"):
            migrate(connection)
    finally:
        connection.close()


def test_fresh_and_upgraded_schemas_are_equivalent(tmp_path: Path) -> None:
    upgraded = factory(tmp_path / "upgraded")
    connection = upgraded.connect()
    try:
        migrate(connection, registry=(Migration(1, "0001_initial.sql"),))
        migrate(connection)
        upgraded_sql = {
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index')"
            )
            if row[0]
        }
    finally:
        connection.close()
    fresh = initialized_factory(tmp_path / "fresh")
    connection = fresh.connect()
    try:
        fresh_sql = {
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index')"
            )
            if row[0]
        }
    finally:
        connection.close()
    assert upgraded_sql == fresh_sql


def test_migration_v4_to_v5_preserves_valid_artifact(tmp_path: Path) -> None:
    db = factory(tmp_path)
    connection = db.connect()
    try:
        migrate(connection, registry=MIGRATIONS[:4])
        now = datetime.now(UTC).isoformat()
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES (?,?, 'consumed',?,?,?)",
            ("session-v4-000001", "v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,'queued',?,?,?,?)",
            (
                "job-v4-000000000001",
                1,
                "v1:" + "b" * 64,
                "v4-test",
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO job_artifacts(job_id,artifact_type,retention_kind,"
            "storage_state,relative_path,byte_size,expires_at,created_at) "
            "VALUES (1,'cache','intermediate','ready',?,1,?,?)",
            (
                "jobs/job-v4-000000000001/work/" + "a" * 32 + ".bin",
                now,
                now,
            ),
        )
        connection.commit()
        assert schema_version(connection) == 4
        assert migrate(connection, registry=MIGRATIONS[:5]) == 5
        row = connection.execute(
            "SELECT storage_state,byte_size,created_at,updated_at "
            "FROM job_artifacts WHERE id=1"
        ).fetchone()
        assert tuple(row) == ("ready", 1, now, now)
    finally:
        connection.close()


def test_v2_failure_metrics_are_grouped_during_v3_upgrade(tmp_path: Path) -> None:
    db = factory(tmp_path)
    connection = db.connect()
    try:
        migrate(connection, registry=MIGRATIONS[:2])
        for error in ("processing_failed", "transcription_failed"):
            connection.execute(
                "INSERT INTO job_failure_metrics(date,error_code,outcome_type,count,"
                "attempt_count,duration_ms,input_tokens,output_tokens,"
                "provider_cost_micro_eur) VALUES "
                "('2026-07-15', ?, 'failed', 1, 2, 3, 5, 7, 11)",
                (error,),
            )
        connection.commit()
        assert migrate(connection) == LATEST_VERSION
        row = connection.execute(
            "SELECT job_count,failed_attempt_count,execution_duration_ms,"
            "actual_input_tokens,actual_output_tokens,actual_cost_micro_eur "
            "FROM job_failure_metrics WHERE date='2026-07-15' AND final_status='failed'"
        ).fetchone()
        assert tuple(row) == (2, 4, 6, 10, 14, 22)
    finally:
        connection.close()


def test_v3_normalizes_legacy_zip_metric(tmp_path: Path) -> None:
    db = factory(tmp_path)
    connection = db.connect()
    try:
        migrate(connection, registry=MIGRATIONS[:2])
        now = datetime.now(UTC).isoformat()
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES (?,?, 'consumed',?,?,?)",
            ("session-0000000001", "v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,created_at,updated_at) VALUES (?,?,?,'completed',?,?,?)",
            ("job-0000000000001", 1, "v1:" + "a" * 64, "v1", now, now),
        )
        connection.execute(
            "INSERT INTO job_metrics(job_id,job_type,pipeline_version,completed_at,"
            "duration_ms,attempt_count) VALUES (1,'zip','v1',?,1,1)",
            (now,),
        )
        connection.execute("UPDATE jobs SET job_type='zip' WHERE id=1")
        connection.commit()
        migrate(connection)
        assert (
            connection.execute("SELECT job_type FROM job_metrics").fetchone()[0]
            == "audio"
        )
        assert connection.execute("SELECT job_type FROM jobs").fetchone()[0] == "audio"
    finally:
        connection.close()


def test_foreign_key_and_parameterization_protect_integrity(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    connection = db.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO upload_files(public_id, session_id, status, storage_path, "
                "declared_bytes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("file-000000000001", 999, "created", "x", 0, "t", "t"),
            )
        value = "x'); DROP TABLE jobs; --"
        connection.execute(
            "INSERT INTO provider_circuits(provider, operation_family, state, "
            "updated_at) VALUES (?, ?, 'closed', ?)",
            (value, "test", "t"),
        )
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        connection.close()


def test_hmac_and_idempotency_reservation_are_durable(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    hmac_service = SecretHmac(b"k" * 32)
    service = IdempotencyService(db, hmac_service)
    assert hmac_service.verify("secret", hmac_service.digest("secret", "link"), "link")
    assert not hmac_service.verify(
        "wrong", hmac_service.digest("secret", "link"), "link"
    )
    first = service.reserve("create", "owner", "key", {"a": [1, 2]})
    assert first.in_progress and first.completed_result is None
    service.complete("create", "owner", "key", {"id": "opaque"})
    assert service.reserve(
        "create", "owner", "key", {"a": [1, 2]}
    ).completed_result == {"id": "opaque"}
    with pytest.raises(IdempotencyConflict):
        service.reserve("create", "owner", "key", {"a": [2, 1]})


def test_domain_values_reject_secrets_times_statuses_and_paths() -> None:
    with pytest.raises(ValueError, match="digest"):
        UploadSession("session-0000000001", "secret", "2026-07-15T00:00:00Z")
    with pytest.raises(ValueError, match="UTC"):
        UploadSession(
            "session-0000000001", "v1:" + "a" * 64, "2026-07-15T00:00:00+02:00"
        )
    with pytest.raises(ValueError, match="storage path"):
        validate_relative_storage_path(r"C:\private\artifact")
    with pytest.raises(ValueError, match="storage path"):
        validate_relative_storage_path("../artifact")


def test_idempotency_is_deterministic_across_connections(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    service = IdempotencyService(db, SecretHmac(b"r" * 32, previous={2: b"o" * 32}))
    reservations: list[bool] = []

    def reserve() -> None:
        reservations.append(
            service.reserve("create", "owner", "shared", {"a": 1}).in_progress
        )

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert reservations == [True, True]
    old = SecretHmac(b"o" * 32, version=2).digest("secret", "link")
    assert service._hmac.verify("secret", old, "link")
    with pytest.raises(IdempotencyConflict):
        service.reserve("create", "owner", "shared", {"a": 2})
    with pytest.raises(ValueError, match="deep"):
        service.reserve("create", "owner", "new", [[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]])


def test_idempotency_survives_hmac_key_rotation(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    v1 = IdempotencyService(db, SecretHmac(b"a" * 32, version=1))
    v1.reserve("launch", "owner", "key", {"revision": 1})
    v1.complete("launch", "owner", "key", {"job_id": "job-0000000000001"})
    rotated = IdempotencyService(
        db, SecretHmac(b"b" * 32, version=2, previous={1: b"a" * 32})
    )
    assert rotated.reserve("launch", "owner", "key", {"revision": 1}).completed_result
    with pytest.raises(IdempotencyConflict):
        rotated.reserve("launch", "owner", "key", {"revision": 2})
    with pytest.raises(ValueError, match="duplicate"):
        SecretHmac(b"b" * 32, version=2, previous={2: b"a" * 32})


def test_revision_and_capacity_are_atomic_under_concurrency(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    uploads = UploadRepository(db)
    outcomes: list[str] = []
    lock = threading.Lock()

    def reserve(number: int) -> None:
        try:
            uploads.reserve(
                session(number), max_active_jobs=1, max_reserved_bytes=1_000
            )
            outcome = "reserved"
        except (DatabaseConflict, DatabaseError):
            outcome = "rejected"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=reserve, args=(number,)) for number in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("reserved") == 1
    connection = db.connect()
    try:
        public_id = connection.execute(
            "SELECT public_id FROM upload_sessions"
        ).fetchone()[0]
    finally:
        connection.close()
    revision = uploads.compare_and_set_status(public_id, 1, "uploading")
    assert revision == 2
    with pytest.raises(DatabaseConflict):
        uploads.compare_and_set_status(public_id, 1, "ready")


def test_capacity_never_exceeds_reserved_bytes(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    uploads = UploadRepository(db)
    results: list[str] = []

    def reserve(number: int) -> None:
        try:
            uploads.reserve(
                UploadSession(
                    f"bytes-{number:012d}",
                    "v1:" + "c" * 64,
                    (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    reserved_bytes=700,
                ),
                max_active_jobs=5,
                max_reserved_bytes=1_000,
            )
            results.append("ok")
        except DatabaseConflict:
            results.append("full")

    threads = [threading.Thread(target=reserve, args=(number,)) for number in (1, 2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert sorted(results) == ["full", "ok"]


def test_promotion_creates_one_job_and_initial_attempt(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    uploads = UploadRepository(db)
    uploads.reserve(
        UploadSession(
            "session-0000000001",
            "v1:" + "a" * 64,
            (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            secret_generation=2,
        ),
        max_active_jobs=1,
        max_reserved_bytes=1_000,
    )
    uploads.compare_and_set_status("session-0000000001", 1, "ready")
    connection = db.connect()
    try:
        connection.execute(
            "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
            "declared_bytes,created_at,updated_at) "
            "SELECT ?,id,'ready','uploads/input',0,?,? FROM upload_sessions "
            "WHERE public_id = ?",
            (
                "file-0000000000001",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                "session-0000000001",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    JobRepository(db).promote(
        Job(
            "job-0000000000001",
            "session-0000000001",
            "v1:" + "a" * 64,
            "v1",
            (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        ),
        expected_session_revision=2,
    )
    connection = db.connect()
    try:
        assert tuple(
            connection.execute("SELECT status,secret_generation FROM jobs").fetchone()
        ) == ("queued", 2)
        assert (
            connection.execute("SELECT status FROM upload_sessions").fetchone()[0]
            == "consumed"
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM upload_files WHERE job_id IS NOT NULL"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_concurrent_promotions_create_one_job(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    uploads = UploadRepository(db)
    uploads.reserve(session(), max_active_jobs=1, max_reserved_bytes=1_000)
    uploads.compare_and_set_status("session-0000000001", 1, "ready")
    connection = db.connect()
    try:
        connection.execute(
            "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
            "declared_bytes,created_at,updated_at) VALUES (?,1,'ready','input',0,?,?)",
            ("file-0000000000001", utc_now(), utc_now()),
        )
        connection.commit()
    finally:
        connection.close()
    outcomes: list[str] = []

    def promote(number: int) -> None:
        try:
            JobRepository(db).promote(
                Job(
                    f"job-{number:013d}",
                    "session-0000000001",
                    "v1:" + "a" * 64,
                    "v1",
                    expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                ),
                expected_session_revision=2,
            )
            outcomes.append("ok")
        except DatabaseConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=promote, args=(item,)) for item in (1, 2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert sorted(outcomes) == ["conflict", "ok"]
    connection = db.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 1
        )
    finally:
        connection.close()


def test_metrics_are_upserted_by_outcome_without_merging_cancellation(
    tmp_path: Path,
) -> None:
    db = initialized_factory(tmp_path)
    jobs = JobRepository(db)
    for outcome in ("failed", "cancelled", "failed"):
        jobs.record_failure_metrics(
            day="2026-07-15",
            final_status=outcome,
            job_count=1,
            failed_attempt_count=1 if outcome == "failed" else 0,
            waiting_duration_ms=1,
            execution_duration_ms=2,
            actual_input_tokens=3,
            actual_output_tokens=5,
            actual_cost_micro_eur=7,
        )
    connection = db.connect()
    try:
        assert (
            connection.execute(
                "SELECT job_count FROM job_failure_metrics "
                "WHERE final_status = 'failed'"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT job_count FROM job_failure_metrics "
                "WHERE final_status = 'cancelled'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_job_metrics_survive_operational_job_purge(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    now = utc_now()
    connection = db.connect()
    try:
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES (?,?,'consumed',?,?,?)",
            ("session-0000000001", "v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,'running',?,?,?,?)",
            ("job-0000000000001", 1, "v1:" + "a" * 64, "v1", now, now, now),
        )
        connection.commit()
    finally:
        connection.close()
    repository = JobRepository(db)
    metrics = JobMetricsRecord(
        "job-0000000000001",
        input_size_bytes=12_345,
        input_file_count=2,
        input_tokens=101,
        output_tokens=202,
        duration_ms=303,
        provider_cost_micro_eur=404,
    )
    with pytest.raises(DatabaseConflict):
        repository.record_job_metrics(metrics)
    repository.compare_and_set_status("job-0000000000001", 1, "failed")
    with pytest.raises(DatabaseConflict):
        repository.record_job_metrics(metrics)
    repository.compare_and_set_status("job-0000000000001", 2, "completed")
    repository.record_job_metrics(metrics)
    with pytest.raises(DatabaseConflict):
        repository.record_job_metrics(metrics)
    connection = db.connect()
    try:
        connection.execute(
            "DELETE FROM jobs WHERE public_id = ?", (metrics.job_public_id,)
        )
        connection.commit()
        row = connection.execute(
            "SELECT input_size_bytes,input_file_count,input_tokens,output_tokens,"
            "duration_ms,provider_cost_micro_eur FROM job_metrics"
        ).fetchone()
        assert tuple(row) == (12_345, 2, 101, 202, 303, 404)
    finally:
        connection.close()


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_database_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = tmp_path / "outside.sqlite3"
    target.write_text("not a database", encoding="utf-8")
    link = root / "tara.sqlite3"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(DatabaseError, match="symbolic"):
        ConnectionFactory(link, root).connect()


def test_storage_root_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "runtime"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(DatabaseError, match="symbolic"):
        ConnectionFactory(link / "tara.sqlite3", link).connect()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_posix_database_file_permissions_are_rejected(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    db.path.chmod(0o644)
    with pytest.raises(DatabaseError, match="permissions"):
        db.connect()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_posix_database_root_0755_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o755)
    with pytest.raises(DatabaseError, match="permissions"):
        ConnectionFactory(root / "tara.sqlite3", root).connect()


def test_corrupt_database_is_refused_without_schema_change(tmp_path: Path) -> None:
    db = initialized_factory(tmp_path)
    db.path.write_bytes(b"not sqlite")
    with pytest.raises(DatabaseError):
        db.connect()


def test_backup_partial_is_removed_after_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tara_web.db import migrations

    db = initialized_factory(tmp_path)
    connection = db.connect()
    backups = tmp_path / "backups"
    monkeypatch.setattr(
        migrations,
        "_assert_integrity",
        lambda _: (_ for _ in ()).throw(
            DatabaseError("database integrity check failed")
        ),
    )
    try:
        with pytest.raises(DatabaseError, match="integrity"):
            migrations._backup(connection, db.path, backups)
        assert not list(backups.glob("*.sqlite3"))
    finally:
        connection.close()
