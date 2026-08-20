from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.storage.artifacts import ArtifactPolicy, ArtifactService
from tara_web.storage.backup import (
    BackupError,
    cleanup_expired_backups,
    create_backup,
    restore_backup,
)
from tara_web.storage.layout import StorageLayout

PUBLIC_RESULT = b"""schema_name: tara.public_result
schema_version: 26.0.1
content:
  title: Backup result
  sections:
    - section_id: overview-backup-result
      section_type: overview
      title: Backup result
      blocks:
        - type: paragraph
          text: Durable.
"""
KEY = b"backup-test-signing-key-is-at-least-32-bytes"


def _source(tmp_path: Path) -> tuple[ConnectionFactory, StorageLayout, int]:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "db" / "tara.sqlite3", root)
    (root / "db").mkdir(mode=0o700)
    connection = factory.connect()
    now = datetime.now(UTC)
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES (?,?, 'consumed',?,?,?)",
            (
                "session-000000000001",
                "v1:" + "a" * 64,
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        cursor = connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,expires_at,created_at,updated_at,finished_at) "
            "VALUES (?,?,?,'completed','test',?,?,?,?)",
            (
                "job-0000000000000001",
                1,
                "v1:" + "a" * 64,
                (now + timedelta(days=7)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.commit()
        job_id = int(cursor.lastrowid)
    finally:
        connection.close()
    layout = StorageLayout(root)
    outcome = ArtifactService(
        layout,
        ArtifactRepository(factory),
        ArtifactPolicy(max_bytes=4096, minimum_free_bytes=0),
    ).write(
        job_id=job_id,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=(PUBLIC_RESULT,),
    )
    assert outcome.error_code is None
    return factory, layout, outcome.artifact_id


def test_backup_and_offline_restore_copy_only_database_and_live_final_yaml(
    tmp_path: Path,
) -> None:
    factory, layout, artifact_id = _source(tmp_path)
    result = create_backup(factory, layout, tmp_path / "backups", KEY)
    assert result.artifact_count == 1
    assert not (result.path / "payload" / "uploads").exists()

    restored = restore_backup(result.path, tmp_path / "restored", KEY)
    restored_factory = ConnectionFactory(restored / "db" / "tara.sqlite3", restored)
    row = ArtifactRepository(restored_factory).get_final_ready(
        artifact_id=artifact_id, job_id=1
    )
    assert (restored / str(row["relative_path"])).read_bytes() == PUBLIC_RESULT
    assert not (restored / "uploads").exists()


def test_restore_validates_database_from_read_only_generation(tmp_path: Path) -> None:
    factory, layout, _ = _source(tmp_path)
    result = create_backup(factory, layout, tmp_path / "backups", KEY)
    database = result.path / "state.sqlite3"
    database.chmod(0o400)
    result.path.chmod(0o500)
    try:
        restored = restore_backup(result.path, tmp_path / "read-only-restore", KEY)
        assert (restored / "db" / "tara.sqlite3").is_file()
    finally:
        result.path.chmod(0o700)
        database.chmod(0o600)


def test_restore_rejects_modified_manifest_before_creating_target(
    tmp_path: Path,
) -> None:
    factory, layout, _ = _source(tmp_path)
    result = create_backup(factory, layout, tmp_path / "backups", KEY)
    manifest = result.path / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    target = tmp_path / "rejected"
    with pytest.raises(BackupError, match="authentication"):
        restore_backup(result.path, target, KEY)
    assert not target.exists()


def test_restore_rejects_authenticated_expired_result_before_activation(
    tmp_path: Path,
) -> None:
    factory, layout, _ = _source(tmp_path)
    result = create_backup(factory, layout, tmp_path / "backups", KEY)
    manifest_path = result.path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifacts"][0]["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_path.write_bytes(encoded)
    (result.path / "manifest.hmac").write_text(
        hmac.new(KEY, encoded, hashlib.sha256).hexdigest(), encoding="ascii"
    )
    target = tmp_path / "expired"
    with pytest.raises(BackupError, match="expired"):
        restore_backup(result.path, target, KEY)
    assert not target.exists()
    assert cleanup_expired_backups(
        tmp_path / "backups", KEY, now=datetime.now(UTC)
    ) == 1
    assert not result.path.exists()


def test_restore_rejects_authenticated_future_schema(tmp_path: Path) -> None:
    factory, layout, _ = _source(tmp_path)
    result = create_backup(factory, layout, tmp_path / "backups", KEY)
    manifest_path = result.path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["schema_version"] = 10_000
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_path.write_bytes(encoded)
    (result.path / "manifest.hmac").write_text(
        hmac.new(KEY, encoded, hashlib.sha256).hexdigest(), encoding="ascii"
    )
    target = tmp_path / "future"
    with pytest.raises(BackupError, match="newer"):
        restore_backup(result.path, target, KEY)
    assert not target.exists()


def test_restore_rejects_rollback_checkpoint_and_payload_symlink(
    tmp_path: Path,
) -> None:
    factory, layout, _ = _source(tmp_path)
    created = datetime.now(UTC)
    result = create_backup(
        factory, layout, tmp_path / "backups", KEY, now=created
    )
    with pytest.raises(BackupError, match="checkpoint"):
        restore_backup(
            result.path,
            tmp_path / "rollback",
            KEY,
            now=created,
            minimum_created_at=created + timedelta(seconds=1),
        )
    manifest = json.loads((result.path / "manifest.json").read_bytes())
    payload = result.path / "payload" / manifest["artifacts"][0]["relative_path"]
    external = tmp_path / "external.yaml"
    external.write_bytes(payload.read_bytes())
    payload.unlink()
    try:
        payload.symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(BackupError, match="symbolic"):
        restore_backup(result.path, tmp_path / "symlink", KEY, now=created)
    assert not (tmp_path / "symlink").exists()
