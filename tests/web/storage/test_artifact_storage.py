from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.storage.artifacts import ArtifactPolicy, ArtifactService
from tara_web.storage.atomic import read_regular, unlink_regular, write_staged
from tara_web.storage.cleanup import (
    cleanup_expired,
    cleanup_orphans,
    delete_artifact,
)
from tara_web.storage.layout import StorageError, StorageLayout
from tara_web.storage.reconciliation import reconcile

PUBLIC_RESULT_YAML = b"""schema_name: tara.public_result
schema_version: 26.0.1
content:
  title: Stored public result
  sections:
    - section_id: overview-stored-public-result
      section_type: overview
      title: Stored public result
      blocks:
        - type: paragraph
          text: Stored public result.
"""


def initialized(tmp_path: Path) -> tuple[ConnectionFactory, StorageLayout, int, int]:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    connection = factory.connect()
    try:
        migrate(connection)
        now = datetime.now(UTC).isoformat()
        for number in (1, 2):
            connection.execute(
                "INSERT INTO upload_sessions("
                "public_id,secret_hmac,status,expires_at,created_at,updated_at"
                ") VALUES (?,?, 'consumed',?,?,?)",
                (f"session-{number:010d}", "v1:" + "a" * 64, now, now, now),
            )
            connection.execute(
                "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
                "pipeline_version,expires_at,created_at,updated_at) "
                "VALUES (?,?,?,'queued',?,?,?,?)",
                (f"job-{number:016d}", number, "v1:" + "a" * 64, "test", now, now, now),
            )
        connection.commit()
        return factory, StorageLayout(root), 1, 2
    finally:
        connection.close()


def service(factory: ConnectionFactory, layout: StorageLayout) -> ArtifactService:
    return ArtifactService(
        layout,
        ArtifactRepository(factory),
        ArtifactPolicy(max_bytes=1024, minimum_free_bytes=0, max_per_job=3),
    )


def test_write_uses_job_authority_and_read_loads_metadata(tmp_path: Path) -> None:
    factory, layout, job_a, job_b = initialized(tmp_path)
    outcome = service(factory, layout).write(
        job_id=job_a,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[PUBLIC_RESULT_YAML],
        validator=lambda path: None,
    )
    assert outcome.error_code is None
    row = ArtifactRepository(factory).get_final_ready(
        artifact_id=outcome.artifact_id, job_id=job_a
    )
    assert str(row["relative_path"]).startswith("jobs/job-0000000000000001/result/")
    assert (
        service(factory, layout).read_final_yaml(
            artifact_id=outcome.artifact_id, job_id=job_a
        )
        == PUBLIC_RESULT_YAML
    )
    with pytest.raises(DatabaseConflict):
        service(factory, layout).read_final_yaml(
            artifact_id=outcome.artifact_id, job_id=job_b
        )


def test_repository_rejects_cross_job_path_and_invalid_area(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    other = layout.new_artifact("job-0000000000000002", "cache")
    with pytest.raises(DatabaseConflict):
        ArtifactRepository(factory).create_pending(
            job_id=job_a,
            artifact_type="cache",
            retention_kind="intermediate",
            relative_path=other.relative_path,
            original_filename=None,
            max_per_job=3,
        )
    with pytest.raises(StorageError):
        layout.new_artifact("job-0000000000000001", "unknown")


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/a",
        "../a",
        "jobs/job-0000000000000001/work/a/b.bin",
        "jobs/job-0000000000000001/work/a.bin",
        "jobs/job-0000000000000001/work/" + "a" * 32 + ".bin.exe",
        "jobs/job-0000000000000001/work/" + "a" * 32 + ".b\u0456n",
        "jobs\\job-0000000000000001\\work\\a.bin",
    ],
)
def test_layout_rejects_noncanonical_paths(tmp_path: Path, value: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    with pytest.raises(StorageError):
        StorageLayout(root).parse_artifact_path(value)


def test_staged_survives_rename_and_reconciliation(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    repository = ArtifactRepository(factory)
    destination = layout.new_artifact("job-0000000000000001", "cache")
    artifact_id = repository.create_pending(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        relative_path=destination.relative_path,
        original_filename=None,
        max_per_job=3,
    )
    with pytest.raises(RuntimeError):
        write_staged(
            layout,
            destination,
            [b"complete"],
            max_bytes=10,
            minimum_free_bytes=0,
            hash_content=False,
            stage=lambda _: (_ for _ in ()).throw(RuntimeError()),
        )
    reconcile(layout, repository)
    assert repository.reconciliation_batch(10) == []
    destination = layout.new_artifact("job-0000000000000001", "cache")
    artifact_id = repository.create_pending(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        relative_path=destination.relative_path,
        original_filename=None,
        max_per_job=3,
    )
    result = write_staged(
        layout,
        destination,
        [b"complete"],
        max_bytes=10,
        minimum_free_bytes=0,
        hash_content=False,
        stage=lambda item: repository.stage(
            artifact_id,
            byte_size=item.byte_size,
            sha256_hex=item.sha256_hex,
            temporary_name=item.temporary_name,
        ),
    )
    assert result.byte_size == 8
    reconcile(layout, repository)
    assert ArtifactRepository(factory).reconciliation_batch(10) == []


def test_staged_temporary_is_promoted_after_crash(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    repository = ArtifactRepository(factory)
    destination = layout.new_artifact("job-0000000000000001", "cache")
    artifact_id = repository.create_pending(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        relative_path=destination.relative_path,
        original_filename=None,
        max_per_job=3,
    )
    temporary = f".{destination.filename}.{'b' * 32}.tmp"
    temp_path = layout.artifact_path(destination.relative_path).with_name(temporary)
    temp_path.write_bytes(b"recovered")
    if os.name != "nt":
        os.chmod(temp_path, 0o600)
    repository.stage(
        artifact_id,
        byte_size=9,
        sha256_hex=None,
        temporary_name=temporary,
    )
    reconcile(layout, repository)
    assert layout.artifact_path(destination.relative_path).read_bytes() == b"recovered"
    assert repository.reconciliation_batch(10) == []


def test_hash_mismatch_marks_ready_final_error_without_replacing_hash(
    tmp_path: Path,
) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    api = service(factory, layout)
    outcome = api.write(
        job_id=job_a,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[PUBLIC_RESULT_YAML],
        validator=lambda path: None,
    )
    row = ArtifactRepository(factory).get_final_ready(
        artifact_id=outcome.artifact_id, job_id=job_a
    )
    path = layout.artifact_path(str(row["relative_path"]))
    path.write_bytes(b"tampered")
    with pytest.raises(StorageError, match="result_integrity_failed"):
        api.read_final_yaml(artifact_id=outcome.artifact_id, job_id=job_a)
    connection = factory.connect()
    try:
        state, digest, code = connection.execute(
            "SELECT storage_state,sha256_hex,storage_error_code "
            "FROM job_artifacts WHERE id=?",
            (outcome.artifact_id,),
        ).fetchone()
        assert (state, digest, code) == (
            "error",
            row["sha256_hex"],
            "integrity_failed",
        )
    finally:
        connection.close()


def test_delete_attempts_are_counted_and_deleted_is_idempotent(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    outcome = service(factory, layout).write(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        chunks=[b"x"],
    )
    repository = ArtifactRepository(factory)
    assert delete_artifact(
        layout, repository, outcome.artifact_id, sleeper=lambda _: None
    )
    assert delete_artifact(
        layout, repository, outcome.artifact_id, sleeper=lambda _: None
    )
    connection = factory.connect()
    try:
        state, attempts, original = connection.execute(
            "SELECT storage_state,delete_attempt_count,original_filename "
            "FROM job_artifacts WHERE id=?",
            (outcome.artifact_id,),
        ).fetchone()
        assert state == "deleted"
        assert attempts == 1
        assert original is None
    finally:
        connection.close()


def test_expired_cleanup_is_bounded_and_uses_utc(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    api = service(factory, layout)
    for _ in range(2):
        api.write(
            job_id=job_a,
            artifact_type="cache",
            retention_kind="intermediate",
            chunks=[b"x"],
        )
    connection = factory.connect()
    try:
        connection.execute(
            "UPDATE job_artifacts SET expires_at=?",
            ((datetime.now(UTC) - timedelta(days=2)).isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()
    assert (
        cleanup_expired(
            layout, ArtifactRepository(factory), batch_size=1, sleeper=lambda _: None
        )
        == 1
    )


def test_orphan_cleanup_checks_each_candidate_not_a_partial_reference_set(
    tmp_path: Path,
) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    layout.create_job_layout("job-0000000000000001")
    directory = layout.root / "jobs" / "job-0000000000000001" / "work"
    candidate = directory / ("0" * 32 + ".bin")
    candidate.write_bytes(b"keep")
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(candidate, (old, old))
    connection = factory.connect()
    try:
        now = datetime.now(UTC).isoformat()
        for number in range(101):
            name = f"{number:032x}.bin"
            connection.execute(
                "INSERT INTO job_artifacts("
                "job_id,artifact_type,retention_kind,storage_state,relative_path,"
                "byte_size,expires_at,created_at,updated_at) "
                "VALUES (?,'cache','intermediate','ready',?,0,?,?,?)",
                (job_a, f"jobs/job-0000000000000001/work/{name}", now, now, now),
            )
        connection.commit()
    finally:
        connection.close()
    assert (
        cleanup_orphans(
            layout,
            ArtifactRepository(factory),
            grace_seconds=3600,
            batch_size=1,
        ).removed
        == 0
    )
    assert candidate.exists()


def test_orphan_cleanup_resumes_after_cursor(tmp_path: Path) -> None:
    from tara_web.storage.cleanup import OrphanScanner

    factory, layout, _, _ = initialized(tmp_path)
    layout.create_job_layout("job-0000000000000001")
    directory = layout.root / "jobs" / "job-0000000000000001" / "work"
    for number in range(2):
        path = directory / f"{number:032x}.bin"
        path.write_bytes(b"orphan")
        old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
        os.utime(path, (old, old))
    scanner = OrphanScanner(layout)
    first = cleanup_orphans(
        layout,
        ArtifactRepository(factory),
        grace_seconds=3600,
        batch_size=1,
        scanner=scanner,
    )
    assert first.inspected == 1
    second = cleanup_orphans(
        layout,
        ArtifactRepository(factory),
        grace_seconds=3600,
        batch_size=1,
        scanner=scanner,
    )
    assert second.inspected == 1


def test_anonymous_temporary_is_removed_after_grace(tmp_path: Path) -> None:
    from tara_web.storage.cleanup import OrphanScanner

    factory, layout, _, _ = initialized(tmp_path)
    layout.create_job_layout("job-0000000000000001")
    directory = layout.root / "jobs" / "job-0000000000000001" / "work"
    temporary = directory / ("." + "a" * 32 + ".bin." + "b" * 32 + ".tmp")
    temporary.write_bytes(b"temp")
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(temporary, (old, old))
    result = cleanup_orphans(
        layout,
        ArtifactRepository(factory),
        grace_seconds=3600,
        batch_size=10,
        scanner=OrphanScanner(layout),
    )
    assert result.removed == 1 and not temporary.exists()


def test_referenced_staged_temporary_is_preserved(tmp_path: Path) -> None:
    from tara_web.storage.cleanup import OrphanScanner

    factory, layout, job_a, _ = initialized(tmp_path)
    repository = ArtifactRepository(factory)
    destination = layout.new_artifact("job-0000000000000001", "cache")
    artifact_id = repository.create_pending(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        relative_path=destination.relative_path,
        original_filename=None,
        max_per_job=10,
    )
    temporary = f".{destination.filename}.{'b' * 32}.tmp"
    path = layout.artifact_path(destination.relative_path).with_name(temporary)
    path.write_bytes(b"temp")
    repository.stage(
        artifact_id, byte_size=4, sha256_hex=None, temporary_name=temporary
    )
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(path, (old, old))
    cleanup_orphans(
        layout,
        repository,
        grace_seconds=3600,
        batch_size=10,
        scanner=OrphanScanner(layout),
    )
    assert path.exists()


def test_artifact_state_invariants_reject_invalid_insert_and_update(
    tmp_path: Path,
) -> None:
    factory, _, job_a, _ = initialized(tmp_path)
    connection = factory.connect()
    now = datetime.now(UTC).isoformat()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO job_artifacts("
                "job_id,artifact_type,retention_kind,storage_state,relative_path,"
                "expires_at,created_at,updated_at) VALUES (?,?,?,'staged',?,?,?,?)",
                (
                    job_a,
                    "cache",
                    "intermediate",
                    "jobs/job-0000000000000001/work/" + "a" * 32 + ".bin",
                    now,
                    now,
                    now,
                ),
            )
        connection.rollback()
        connection.execute(
            "INSERT INTO job_artifacts("
            "job_id,artifact_type,retention_kind,storage_state,relative_path,"
            "byte_size,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,'ready',?,0,?,?,?)",
            (
                job_a,
                "cache",
                "intermediate",
                "jobs/job-0000000000000001/work/" + "b" * 32 + ".bin",
                now,
                now,
                now,
            ),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE job_artifacts SET storage_state='deleted' WHERE id=1"
            )
    finally:
        connection.close()


def test_missing_artifact_deletion_returns_warning(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    outcome = service(factory, layout).write(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        chunks=[b"x"],
    )
    connection = factory.connect()
    try:
        relative_path = connection.execute(
            "SELECT relative_path FROM job_artifacts WHERE id=?",
            (outcome.artifact_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    layout.artifact_path(str(relative_path)).unlink()
    result = delete_artifact(
        layout, ArtifactRepository(factory), outcome.artifact_id, sleeper=lambda _: None
    )
    assert result.success and result.warning_code == "artifact_missing"


def test_failed_delete_counts_all_three_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tara_web.storage.cleanup as cleanup_module

    factory, layout, job_a, _ = initialized(tmp_path)
    outcome = service(factory, layout).write(
        job_id=job_a,
        artifact_type="cache",
        retention_kind="intermediate",
        chunks=[b"x"],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(
        cleanup_module,
        "unlink_regular",
        lambda *args, **kwargs: (_ for _ in ()).throw(StorageError("failed")),
    )
    result = delete_artifact(
        layout,
        ArtifactRepository(factory),
        outcome.artifact_id,
        sleeper=sleeps.append,
    )
    assert not result.success
    assert sleeps == [0.1, 0.5]
    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT storage_state,storage_error_code,delete_attempt_count "
            "FROM job_artifacts WHERE id=?",
            (outcome.artifact_id,),
        ).fetchone()
        assert tuple(row) == ("error", "delete_failed", 3)
    finally:
        connection.close()


def test_final_yaml_uses_mandatory_default_validator(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    api = service(factory, layout)
    outcome = api.write(
        job_id=job_a,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[PUBLIC_RESULT_YAML],
    )
    assert outcome.error_code is None
    assert api.read_final_yaml(artifact_id=outcome.artifact_id, job_id=job_a) == (
        PUBLIC_RESULT_YAML
    )
    arbitrary = api.write(
        job_id=job_a,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[b"summary: valid\n"],
    )
    assert arbitrary.error_code == "artifact_write_failed"


def test_final_yaml_rejects_public_absolute_path(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    outcome = service(factory, layout).write(
        job_id=job_a,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[
            b"""schema_name: tara.public_result
schema_version: 26.0.1
content:
  title: Public result
  sections:
    - section_id: overview-public-result
      section_type: overview
      title: Public result
      blocks:
        - type: paragraph
          text: /var/private/transcript.txt
"""
        ],
    )
    assert outcome.error_code == "artifact_write_failed"


def test_final_yaml_validation_failure_is_integrity_error(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    outcome = service(factory, layout).write(
        job_id=job_a,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=[b"- not-a-mapping\n"],
    )
    assert outcome.error_code == "artifact_write_failed"
    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT storage_state,storage_error_code FROM job_artifacts WHERE id=?",
            (outcome.artifact_id,),
        ).fetchone()
        assert tuple(row) == ("error", "integrity_failed")
    finally:
        connection.close()


def test_artifact_policy_limits_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, layout, job_a, job_b = initialized(tmp_path)
    too_small = ArtifactService(
        layout,
        ArtifactRepository(factory),
        ArtifactPolicy(max_bytes=1, minimum_free_bytes=0, max_per_job=3),
    )
    assert (
        too_small.write(
            job_id=job_a,
            artifact_type="cache",
            retention_kind="intermediate",
            chunks=[b"xx"],
        ).warning_code
        == "service_degraded"
    )

    monkeypatch.setattr(
        "tara_web.storage.atomic.shutil.disk_usage",
        lambda _: SimpleNamespace(free=0),
    )
    no_space = ArtifactService(
        layout,
        ArtifactRepository(factory),
        ArtifactPolicy(max_bytes=10, minimum_free_bytes=1, max_per_job=3),
    )
    assert (
        no_space.write(
            job_id=job_b,
            artifact_type="cache",
            retention_kind="intermediate",
            chunks=[b"x"],
        ).warning_code
        == "service_degraded"
    )

    limited = ArtifactService(
        layout,
        ArtifactRepository(factory),
        ArtifactPolicy(max_bytes=10, minimum_free_bytes=0, max_per_job=1),
    )
    with pytest.raises(DatabaseConflict, match="limit"):
        limited.write(
            job_id=job_a,
            artifact_type="cache",
            retention_kind="intermediate",
            chunks=[b"x"],
        )


def test_cleanup_arguments_require_bounds_and_utc(tmp_path: Path) -> None:
    factory, layout, _, _ = initialized(tmp_path)
    repository = ArtifactRepository(factory)
    with pytest.raises(ValueError, match="batch"):
        cleanup_expired(layout, repository, batch_size=0)
    with pytest.raises(ValueError, match="grace"):
        cleanup_orphans(layout, repository, grace_seconds=0, batch_size=1)
    with pytest.raises(ValueError, match="UTC"):
        cleanup_expired(layout, repository, batch_size=1, now=datetime.now())


def test_reconcile_all_drains_more_than_one_batch(tmp_path: Path) -> None:
    from tara_web.storage.reconciliation import reconcile_all

    factory, layout, job_a, _ = initialized(tmp_path)
    repository = ArtifactRepository(factory)
    for _ in range(2):
        destination = layout.new_artifact("job-0000000000000001", "cache")
        repository.create_pending(
            job_id=job_a,
            artifact_type="cache",
            retention_kind="intermediate",
            relative_path=destination.relative_path,
            original_filename=None,
            max_per_job=10,
        )
    reconcile_all(layout, repository, limit=1, max_bytes=1024, validator=None)
    assert repository.reconciliation_batch(10) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd no-follow semantics")
def test_parent_symlink_swap_cannot_escape_write_read_or_delete(tmp_path: Path) -> None:
    factory, layout, job_a, _ = initialized(tmp_path)
    destination = layout.new_artifact("job-0000000000000001", "cache")
    outside = tmp_path / "outside"
    outside.mkdir()
    destination_path = layout.artifact_path(destination.relative_path)
    destination_path.parent.rmdir()
    destination_path.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        write_staged(
            layout,
            destination,
            [b"x"],
            max_bytes=2,
            minimum_free_bytes=0,
            hash_content=False,
            stage=lambda _: None,
        )
    assert not list(outside.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd no-follow semantics")
def test_parent_swap_after_anchor_cannot_escape_operations(tmp_path: Path) -> None:
    _, layout, _, _ = initialized(tmp_path)
    outside = tmp_path / "outside-after-anchor"
    outside.mkdir()

    def swap(parent: Path) -> tuple[Path, Callable[[], None]]:
        anchored = parent.with_name(f"{parent.name}-anchored")

        def perform() -> None:
            parent.rename(anchored)
            parent.symlink_to(outside, target_is_directory=True)

        return anchored, perform

    def restore(parent: Path, anchored: Path) -> None:
        parent.unlink()
        anchored.rename(parent)

    destination = layout.new_artifact("job-0000000000000001", "cache")
    parent = layout.artifact_path(destination.relative_path).parent
    anchored, perform = swap(parent)
    write_staged(
        layout,
        destination,
        [b"trusted"],
        max_bytes=10,
        minimum_free_bytes=0,
        hash_content=False,
        stage=lambda _: None,
        after_directory_open=perform,
    )
    assert not list(outside.iterdir())
    restore(parent, anchored)

    outside_target = outside / destination.filename
    outside_target.write_bytes(b"outside")
    anchored, perform = swap(parent)
    assert (
        read_regular(
            layout,
            destination,
            expected_bytes=7,
            max_bytes=10,
            after_directory_open=perform,
        )
        == b"trusted"
    )
    assert outside_target.read_bytes() == b"outside"
    restore(parent, anchored)

    anchored, perform = swap(parent)
    assert unlink_regular(layout, destination, after_directory_open=perform)
    assert outside_target.read_bytes() == b"outside"
    restore(parent, anchored)
