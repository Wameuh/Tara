"""Strict, ordered SQLite migration registry."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .connection import DatabaseError


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str


MIGRATIONS = (
    Migration(1, "0001_initial.sql"),
    Migration(2, "0002_jobs_metrics.sql"),
    Migration(3, "0003_metrics_history.sql"),
    Migration(4, "0004_artifact_storage.sql"),
    Migration(5, "0005_artifact_staging.sql"),
    Migration(6, "0006_resumable_audio_upload.sql"),
    Migration(7, "0007_upload_cleanup_state.sql"),
    Migration(8, "0008_orchestration.sql"),
    Migration(9, "0009_relaunch_lineage.sql"),
    Migration(10, "0010_attempt_cost_known.sql"),
    Migration(11, "0011_session_inputs.sql"),
    Migration(12, "0012_provider_usage_attempts.sql"),
    Migration(13, "0013_merged_transcription_upload.sql"),
    Migration(14, "0014_zip_archives.sql"),
    Migration(15, "0015_inference_budget.sql"),
    Migration(16, "0016_resilience.sql"),
    Migration(17, "0017_kofi_funding.sql"),
    Migration(18, "0018_local_admin_dashboard.sql"),
    Migration(19, "0019_aac_m4a_audio.sql"),
)


def schema_version(connection: sqlite3.Connection) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not exists:
        return 0
    row = connection.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()
    return int(row[0]) if row else 0


def migrate(
    connection: sqlite3.Connection,
    *,
    database_path: Path | None = None,
    backups_root: Path | None = None,
    registry: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Apply every registered migration atomically; reject unknown future databases."""
    _validate_registry(registry)
    _assert_integrity(connection)
    current = schema_version(connection)
    latest = registry[-1].version
    if current > latest:
        raise DatabaseError("database schema version is newer than this application")
    pending = [migration for migration in registry if migration.version > current]
    parsed = [
        (migration, _statements(_migration_sql(migration))) for migration in pending
    ]
    if parsed and database_path and backups_root:
        _backup(connection, database_path, backups_root)
    for migration, statements in parsed:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_version (id, version) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
                (migration.version,),
            )
            connection.commit()
        except Exception as exc:
            if connection.in_transaction:
                connection.rollback()
            if isinstance(exc, DatabaseError):
                raise
            raise DatabaseError("database migration failed") from exc
    return latest


def _migration_sql(migration: Migration) -> str:
    return (Path(__file__).parent / "migrations" / migration.filename).read_text(
        encoding="utf-8"
    )


def _assert_integrity(connection: sqlite3.Connection) -> None:
    for pragma in ("quick_check", "integrity_check"):
        rows = connection.execute(f"PRAGMA {pragma}").fetchall()
        if not rows or any(str(row[0]).lower() != "ok" for row in rows):
            raise DatabaseError("database integrity check failed")


def _backup(
    connection: sqlite3.Connection, database_path: Path, backups_root: Path
) -> Path:
    """Create a consistent SQLite backup before changing an existing database."""
    if not database_path.exists() or schema_version(connection) == 0:
        return database_path
    _validate_backup_root(backups_root)
    target = backups_root / (
        f"{database_path.stem}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-"
        f"{uuid.uuid4().hex}.sqlite3"
    )
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        destination = sqlite3.connect(target)
        try:
            connection.backup(destination)
            _assert_integrity(destination)
        finally:
            destination.close()
    except (DatabaseError, OSError, sqlite3.Error) as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError("database backup failed") from exc
    return target


def _validate_registry(registry: Sequence[Migration]) -> None:
    if not registry or any(item.version < 1 for item in registry):
        raise DatabaseError("migration registry is invalid")
    versions = [item.version for item in registry]
    if versions != list(range(1, len(versions) + 1)):
        raise DatabaseError("migration registry is invalid")


def _validate_backup_root(backups_root: Path) -> None:
    if not backups_root.exists():
        old_umask = os.umask(0o077) if os.name != "nt" else None
        try:
            backups_root.mkdir(parents=True, mode=0o700)
        finally:
            if old_umask is not None:
                os.umask(old_umask)
    raw = backups_root.absolute()
    for entry in (raw, *raw.parents):
        if entry.exists() and entry.is_symlink():
            raise DatabaseError("backup storage must not contain symbolic links")
        if entry.parent == entry:
            break
    if os.name != "nt":
        if raw.stat().st_mode & 0o077:
            raise DatabaseError("backup storage permissions are unsafe")
        os.chmod(raw, 0o700)


def _statements(sql: str) -> list[str]:
    """Split a trusted migration with SQLite's own complete-statement parser."""
    statements: list[str] = []
    current = ""
    for line in sql.splitlines():
        if line.lstrip().startswith("--"):
            continue
        current += f"{line}\n"
        if sqlite3.complete_statement(current):
            statement = current.strip()
            if statement:
                statements.append(statement)
            current = ""
    if current.strip():
        raise DatabaseError("migration contains an incomplete SQL statement")
    return statements
