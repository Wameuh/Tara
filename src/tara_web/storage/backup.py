"""Authenticated, SQLite-consistent backups of durable results only."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tara_web.db.connection import ConnectionFactory, DatabaseError
from tara_web.db.migrations import MIGRATIONS, schema_version

from .atomic import read_regular
from .layout import StorageError, StorageLayout

BACKUP_FORMAT_VERSION = 1
_DATABASE_NAME = "state.sqlite3"
_MANIFEST_NAME = "manifest.json"
_SIGNATURE_NAME = "manifest.hmac"
_GENERATION = re.compile(r"^backup-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$")


class BackupError(RuntimeError):
    """Stable operator-facing backup or restore failure."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    artifact_count: int
    expires_at: str | None


def create_backup(
    database: ConnectionFactory,
    layout: StorageLayout,
    backups_root: Path,
    signing_key: bytes,
    *,
    now: datetime | None = None,
) -> BackupResult:
    """Publish one atomic generation containing SQLite and live final YAML only."""
    current = _utc(now)
    _key(signing_key)
    _private_directory(backups_root)
    token = uuid.uuid4().hex
    temporary = backups_root / f".backup-{token}.tmp"
    target = backups_root / f"backup-{current:%Y%m%dT%H%M%SZ}-{token}"
    try:
        _private_directory(temporary, parents=False)
        snapshot_path = temporary / _DATABASE_NAME
        source = database.connect()
        destination = sqlite3.connect(snapshot_path)
        destination.row_factory = sqlite3.Row
        try:
            source.backup(destination)
            destination.execute(
                "UPDATE job_artifacts SET storage_state='deleted',deleted_at=?,"
                "original_filename=NULL WHERE artifact_type='final_yaml' "
                "AND storage_state IN ('ready','error') "
                "AND julianday(expires_at) <= julianday(?)",
                (current.isoformat(), current.isoformat()),
            )
            destination.execute(
                "UPDATE jobs SET status='expired',updated_at=? "
                "WHERE status='completed' AND julianday(expires_at) <= julianday(?)",
                (current.isoformat(), current.isoformat()),
            )
            destination.commit()
            _integrity(destination)
            version = schema_version(destination)
            rows = destination.execute(
                "SELECT relative_path,sha256_hex,byte_size,expires_at "
                "FROM job_artifacts WHERE artifact_type='final_yaml' "
                "AND storage_state='ready' AND sha256_hex IS NOT NULL "
                "AND byte_size IS NOT NULL AND julianday(expires_at) > julianday(?) "
                "ORDER BY relative_path",
                (current.isoformat(),),
            ).fetchall()
        finally:
            destination.close()
            source.close()
        os.chmod(snapshot_path, 0o600)
        artifacts: list[dict[str, object]] = []
        for row in rows:
            relative = str(row["relative_path"])
            managed = layout.parse_artifact_path(relative)
            if managed.directory_parts[2] != "result":
                raise BackupError("backup contains an invalid final result path")
            size = int(row["byte_size"])
            content = read_regular(
                layout, managed, expected_bytes=size, max_bytes=max(1, size)
            )
            digest = hashlib.sha256(content).hexdigest()
            if not hmac.compare_digest(digest, str(row["sha256_hex"])):
                raise BackupError("final result integrity check failed")
            payload = temporary / "payload" / Path(relative)
            _write_private(payload, content)
            artifacts.append(
                {
                    "relative_path": relative,
                    "sha256": digest,
                    "byte_size": size,
                    "expires_at": str(row["expires_at"]),
                }
            )
        database_digest = _file_sha256(snapshot_path)
        manifest = {
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "schema_version": version,
            "created_at": current.isoformat(),
            "database": {"name": _DATABASE_NAME, "sha256": database_digest},
            "artifacts": artifacts,
        }
        encoded = _canonical(manifest)
        _write_private(temporary / _MANIFEST_NAME, encoded)
        _write_private(
            temporary / _SIGNATURE_NAME,
            hmac.new(signing_key, encoded, hashlib.sha256).hexdigest().encode("ascii"),
        )
        _fsync_tree(temporary)
        os.replace(temporary, target)
        _fsync_directory(backups_root)
        expirations = [str(item["expires_at"]) for item in artifacts]
        return BackupResult(target, len(artifacts), min(expirations, default=None))
    except (BackupError, DatabaseError, StorageError, OSError, sqlite3.Error) as exc:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("backup creation failed") from exc


def restore_backup(
    backup_path: Path,
    target_root: Path,
    signing_key: bytes,
    *,
    sqlite_relative_path: Path = Path("db/tara.sqlite3"),
    now: datetime | None = None,
    minimum_created_at: datetime | None = None,
) -> Path:
    """Validate completely, then atomically activate into a new storage root."""
    current = _utc(now)
    _key(signing_key)
    _existing_private_directory(backup_path)
    if target_root.exists():
        raise BackupError("restore target must be a new storage root")
    if sqlite_relative_path.is_absolute() or ".." in sqlite_relative_path.parts:
        raise BackupError("restore database path is invalid")
    encoded = _read_small_regular(backup_path / _MANIFEST_NAME, 4 * 1024 * 1024)
    signature = _read_small_regular(backup_path / _SIGNATURE_NAME, 128).decode("ascii")
    expected = hmac.new(signing_key, encoded, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise BackupError("backup manifest authentication failed")
    try:
        manifest = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    _validate_manifest(manifest, current)
    created_at = datetime.fromisoformat(str(manifest["created_at"]))
    if minimum_created_at is not None:
        minimum = _utc(minimum_created_at)
        if created_at < minimum:
            raise BackupError("backup generation is older than the restore checkpoint")
    database_path = backup_path / _DATABASE_NAME
    if _file_sha256(database_path) != manifest["database"]["sha256"]:
        raise BackupError("backup database integrity check failed")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        _integrity(connection)
        if schema_version(connection) != int(manifest["schema_version"]):
            raise BackupError("backup schema metadata is inconsistent")
    finally:
        connection.close()
    layout = StorageLayout(target_root)
    validated: list[tuple[dict[str, object], Path]] = []
    for item in manifest["artifacts"]:
        relative = str(item["relative_path"])
        managed = layout.parse_artifact_path(relative)
        if managed.directory_parts[2] != "result":
            raise BackupError("backup contains a non-final artifact")
        source = backup_path / "payload" / Path(relative)
        _assert_beneath_without_links(source, backup_path)
        size = int(item["byte_size"])
        content = _read_small_regular(source, size)
        if len(content) != size or _sha256(content) != item["sha256"]:
            raise BackupError("backup result integrity check failed")
        validated.append((item, source))
    parent = target_root.parent
    if not parent.is_absolute() or parent.is_symlink() or not parent.is_dir():
        raise BackupError("restore target parent is invalid")
    staging = parent / f".{target_root.name}.restore-{uuid.uuid4().hex}.tmp"
    try:
        _private_directory(staging, parents=False)
        _copy_private(database_path, staging / sqlite_relative_path)
        staging_layout = StorageLayout(staging)
        for item, source in validated:
            managed = staging_layout.parse_artifact_path(str(item["relative_path"]))
            _copy_private(source, staging / Path(managed.relative_path))
        _fsync_tree(staging)
        os.replace(staging, target_root)
        _fsync_directory(parent)
        return target_root
    except (BackupError, OSError) as exc:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("backup activation failed") from exc


def cleanup_expired_backups(
    backups_root: Path, signing_key: bytes, *, now: datetime | None = None
) -> int:
    """Remove generations as soon as any included source result expires."""
    current = _utc(now)
    _key(signing_key)
    _existing_private_directory(backups_root)
    removed = 0
    for entry in os.scandir(backups_root):
        path = Path(entry.path)
        if not _GENERATION.fullmatch(entry.name):
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise BackupError("backup generation is invalid")
        encoded = _read_small_regular(path / _MANIFEST_NAME, 4 * 1024 * 1024)
        signature = _read_small_regular(path / _SIGNATURE_NAME, 128).decode("ascii")
        expected = hmac.new(signing_key, encoded, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise BackupError("backup manifest authentication failed")
        try:
            manifest = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("backup manifest is invalid") from exc
        _validate_manifest_structure(manifest)
        expirations = [
            datetime.fromisoformat(str(item["expires_at"]))
            for item in manifest["artifacts"]
        ]
        if expirations and min(expirations) <= current:
            shutil.rmtree(path)
            removed += 1
    if removed:
        _fsync_directory(backups_root)
    return removed


def _validate_manifest(manifest: object, now: datetime) -> None:
    _validate_manifest_structure(manifest)
    for item in manifest["artifacts"]:
        try:
            expires = datetime.fromisoformat(item["expires_at"])
        except (TypeError, ValueError) as exc:
            raise BackupError("backup artifact expiration is invalid") from exc
        if expires.tzinfo is None or expires <= now:
            raise BackupError("backup contains an expired final result")


def _validate_manifest_structure(manifest: object) -> None:
    if not isinstance(manifest, dict) or set(manifest) != {
        "backup_format_version",
        "schema_version",
        "created_at",
        "database",
        "artifacts",
    }:
        raise BackupError("backup manifest is invalid")
    if manifest["backup_format_version"] != BACKUP_FORMAT_VERSION:
        raise BackupError("backup format is newer or unsupported")
    try:
        created = datetime.fromisoformat(manifest["created_at"])
    except (TypeError, ValueError) as exc:
        raise BackupError("backup creation time is invalid") from exc
    if created.tzinfo is None:
        raise BackupError("backup creation time is invalid")
    if not isinstance(manifest["schema_version"], int) or not (
        1 <= manifest["schema_version"] <= MIGRATIONS[-1].version
    ):
        raise BackupError("backup schema is newer or unsupported")
    if not isinstance(manifest["database"], dict) or set(manifest["database"]) != {
        "name",
        "sha256",
    }:
        raise BackupError("backup database metadata is invalid")
    if manifest["database"]["name"] != _DATABASE_NAME or not _digest(
        manifest["database"]["sha256"]
    ):
        raise BackupError("backup database metadata is invalid")
    if not isinstance(manifest["artifacts"], list):
        raise BackupError("backup artifact metadata is invalid")
    seen: set[str] = set()
    for item in manifest["artifacts"]:
        if not isinstance(item, dict) or set(item) != {
            "relative_path",
            "sha256",
            "byte_size",
            "expires_at",
        }:
            raise BackupError("backup artifact metadata is invalid")
        relative = item["relative_path"]
        if not isinstance(relative, str) or relative in seen:
            raise BackupError("backup artifact metadata is invalid")
        seen.add(relative)
        if not _digest(item["sha256"]) or not isinstance(item["byte_size"], int):
            raise BackupError("backup artifact metadata is invalid")
        if item["byte_size"] < 0 or item["byte_size"] > 1_073_741_824:
            raise BackupError("backup artifact metadata is invalid")
        try:
            expires = datetime.fromisoformat(item["expires_at"])
        except (TypeError, ValueError) as exc:
            raise BackupError("backup artifact expiration is invalid") from exc
        if expires.tzinfo is None:
            raise BackupError("backup artifact expiration is invalid")


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() != UTC.utcoffset(current):
        raise ValueError("backup time must be UTC")
    return current


def _key(value: bytes) -> None:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ValueError("backup signing key must contain at least 32 bytes")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise BackupError("backup contains an invalid file")
        while chunk := os.read(descriptor, 65536):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _read_small_regular(path: Path, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise BackupError("backup contains an invalid file")
        content = bytearray()
        while chunk := os.read(descriptor, 65536):
            content.extend(chunk)
        return bytes(content)
    except OSError as exc:
        raise BackupError("backup file is unavailable") from exc
    finally:
        os.close(descriptor)


def _write_private(path: Path, content: bytes) -> None:
    _private_directory(path.parent)
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_private(source: Path, destination: Path) -> None:
    _write_private(destination, _read_small_regular(source, 1_073_741_824))


def _private_directory(path: Path, *, parents: bool = True) -> None:
    old_umask = os.umask(0o077) if os.name != "nt" else None
    try:
        path.mkdir(parents=parents, mode=0o700, exist_ok=False if not parents else True)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise BackupError("backup directory is invalid") from None
    finally:
        if old_umask is not None:
            os.umask(old_umask)
    _existing_private_directory(path)


def _existing_private_directory(path: Path) -> None:
    if not path.is_absolute():
        raise BackupError("backup paths must be absolute")
    for entry in (path, *path.parents):
        if entry.exists() and entry.is_symlink():
            raise BackupError("backup paths must not contain symbolic links")
        if entry.parent == entry:
            break
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise BackupError("backup directory is invalid")
    if os.name != "nt" and info.st_mode & 0o077:
        raise BackupError("backup directory permissions are unsafe")


def _assert_beneath_without_links(path: Path, anchor: Path) -> None:
    raw_anchor = anchor.absolute()
    raw_path = path.absolute()
    if not raw_path.is_relative_to(raw_anchor):
        raise BackupError("backup path escapes its generation")
    current = raw_anchor
    for part in raw_path.relative_to(raw_anchor).parts:
        current /= part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise BackupError("backup file is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BackupError("backup paths must not contain symbolic links")


def _integrity(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if not rows or any(str(row[0]).lower() != "ok" for row in rows):
        raise BackupError("backup database integrity check failed")


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _fsync_tree(path: Path) -> None:
    for root, directories, _files in os.walk(path, topdown=False, followlinks=False):
        if any((Path(root) / item).is_symlink() for item in directories):
            raise BackupError("backup contains a symbolic link")
        _fsync_directory(Path(root))
