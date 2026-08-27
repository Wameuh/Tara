"""Bounded artifact retention cleanup with a stateful no-follow scanner."""

from __future__ import annotations

import os
import re
import stat
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tara_web.db.connection import DatabaseConflict
from tara_web.db.repositories.artifacts import ArtifactRepository

from .atomic import unlink_regular, unlink_temporary
from .layout import SAFE_ID, StorageError, StorageLayout

_TEMP = re.compile(r"^\.([a-f0-9]{32}\.(?:bin|yaml))\.([a-f0-9]{32})\.tmp$")
_DELAYS = (0.0, 0.1, 0.5)


@dataclass(frozen=True)
class DeleteOutcome:
    success: bool
    warning_code: str | None = None


@dataclass(frozen=True)
class OrphanCleanupResult:
    inspected: int
    removed: int


class OrphanScanner:
    """Incremental strict-depth scanner; no rglob, materialised list or symlink walk."""

    def __init__(self, layout: StorageLayout) -> None:
        self._layout = layout
        self._jobs: Iterator[os.DirEntry[str]] | None = None
        self._areas: Iterator[os.DirEntry[str]] | None = None
        self._files: Iterator[os.DirEntry[str]] | None = None
        self._job = ""
        self._area = ""

    def next_file(self) -> Path | None:
        root = self._layout.root / "jobs"
        if self._jobs is None:
            try:
                self._jobs = os.scandir(root)
            except OSError:
                return None
        while True:
            if self._files is not None:
                try:
                    entry = next(self._files)
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        return Path()  # invalid entry still consumes one budget slot.
                    return Path(entry.path)
                except StopIteration:
                    self._files.close()  # type: ignore[attr-defined]
                    self._files = None
            if self._areas is not None:
                try:
                    area = next(self._areas)
                    if (
                        area.name not in {"inputs", "work", "result"}
                        or area.is_symlink()
                        or not area.is_dir(follow_symlinks=False)
                    ):
                        return Path()
                    self._area = area.name
                    try:
                        self._files = os.scandir(area.path)
                    except OSError:
                        return Path()
                    continue
                except StopIteration:
                    self._areas.close()  # type: ignore[attr-defined]
                    self._areas = None
            try:
                job = next(self._jobs)
            except StopIteration:
                self._jobs.close()  # type: ignore[attr-defined]
                self._jobs = None
                return None
            if (
                not SAFE_ID.fullmatch(job.name)
                or job.is_symlink()
                or not job.is_dir(follow_symlinks=False)
            ):
                return Path()
            self._job = job.name
            try:
                self._areas = os.scandir(job.path)
            except OSError:
                return Path()

    def close(self) -> None:
        for value in (self._files, self._areas, self._jobs):
            if value is not None:
                value.close()  # type: ignore[attr-defined]
        self._files = self._areas = self._jobs = None


def cleanup_orphans(
    layout: StorageLayout,
    repository: ArtifactRepository,
    *,
    scanner: OrphanScanner | None = None,
    grace_seconds: int,
    batch_size: int,
    now: datetime | None = None,
) -> OrphanCleanupResult:
    _validate_cleanup_arguments(
        batch_size=batch_size, now=now, grace_seconds=grace_seconds
    )
    current = now or datetime.now(UTC)
    scanner = scanner or OrphanScanner(layout)
    removed = inspected = 0
    while inspected < batch_size:
        path = scanner.next_file()
        if path is None:
            break
        inspected += 1
        if not path:
            continue
        try:
            relative = path.relative_to(layout.root).as_posix()
            age = current.timestamp() - path.stat().st_mtime
            temporary = _TEMP.fullmatch(path.name)
            if temporary:
                final = temporary.group(1)
                destination = layout.parse_artifact_path(
                    f"jobs/{scanner._job}/{scanner._area}/{final}"
                )
                if age > grace_seconds and not repository.staged_temp_is_referenced(
                    destination.relative_path, path.name
                ):
                    unlink_temporary(layout, destination, path.name)
                    removed += 1
            else:
                destination = layout.parse_artifact_path(relative)
                if age > grace_seconds and not repository.path_exists(relative):
                    unlink_regular(layout, destination)
                    removed += 1
        except (OSError, StorageError, ValueError):
            continue
    return OrphanCleanupResult(inspected, removed)


def delete_artifact(
    layout: StorageLayout,
    repository: ArtifactRepository,
    artifact_id: int,
    *,
    sleeper: Callable[[float], None] | None = None,
) -> DeleteOutcome:
    sleep = sleeper or time.sleep
    for delay in _DELAYS:
        if delay:
            sleep(delay)
        try:
            relative = repository.begin_delete_attempt(artifact_id)
        except DatabaseConflict:
            return DeleteOutcome(False)
        if relative is None:
            return DeleteOutcome(True)
        try:
            removed = unlink_regular(layout, layout.parse_artifact_path(relative))
            repository.mark_deleted(artifact_id)
            return DeleteOutcome(True, None if removed else "artifact_missing")
        except (StorageError, ValueError):
            continue
    try:
        repository.mark_error(artifact_id, "delete_failed")
    except DatabaseConflict:
        pass
    return DeleteOutcome(False)


def cleanup_expired(
    layout: StorageLayout,
    repository: ArtifactRepository,
    *,
    batch_size: int,
    now: datetime | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> int:
    _validate_cleanup_arguments(batch_size=batch_size, now=now)
    current = now or datetime.now(UTC)
    count = 0
    for row in repository.expired_batch(current.isoformat(), batch_size):
        if delete_artifact(layout, repository, int(row["id"]), sleeper=sleeper).success:
            count += 1
    return count


def cleanup_expired_inputs(
    layout: StorageLayout,
    repository: ArtifactRepository,
    *,
    batch_size: int,
    now: datetime | None = None,
) -> int:
    """Remove legacy tracked inputs as soon as their job is terminal."""
    _validate_cleanup_arguments(batch_size=batch_size, now=now)
    current = now or datetime.now(UTC)
    count = 0
    for row in repository.expired_input_batch(current.isoformat(), batch_size):
        try:
            destination = layout.parse_artifact_path(str(row["destination_path"]))
            unlink_regular(layout, destination)
            repository.remove_input_preparation(int(row["id"]))
            job_id = int(row["job_id"])
            if not repository.job_has_input_preparations(job_id):
                unlink_regular(
                    layout, layout.source_manifest(str(row["public_id"]))
                )
            count += 1
        except (DatabaseConflict, StorageError, ValueError):
            continue
    return count


def cleanup_terminal_private_data(
    layout: StorageLayout,
    repository: ArtifactRepository,
    public_id: str,
) -> bool:
    """Delete all terminal-job private data while preserving a completed result."""
    target = repository.terminal_private_cleanup_target(public_id)
    if target is None:
        return True
    job_public_id = str(target["public_id"])
    session_public_id = str(target["session_public_id"])
    if not SAFE_ID.fullmatch(job_public_id) or not SAFE_ID.fullmatch(session_public_id):
        return False
    preserved_name: str | None = None
    preserved_path = target["preserved_result_path"]
    if preserved_path is not None:
        try:
            managed = layout.parse_artifact_path(str(preserved_path))
        except StorageError:
            return False
        if managed.directory_parts != ("jobs", job_public_id, "result"):
            return False
        preserved_name = managed.filename
    try:
        _remove_tree(layout.root / "jobs" / job_public_id / "inputs")
        _remove_tree(layout.root / "jobs" / job_public_id / "work")
        _remove_tree(layout.root / "uploads" / session_public_id)
        result = layout.root / "jobs" / job_public_id / "result"
        if preserved_name is None:
            _remove_tree(result)
        else:
            _remove_children(result, preserved_name=preserved_name)
    except (OSError, StorageError):
        return False
    return repository.complete_terminal_private_cleanup(
        int(target["id"]),
        str(target["status"]),
        (
            int(target["preserved_artifact_id"])
            if target["preserved_artifact_id"] is not None
            else None
        ),
    )


def cleanup_terminal_private_batch(
    layout: StorageLayout,
    repository: ArtifactRepository,
    *,
    batch_size: int,
) -> int:
    _validate_cleanup_arguments(batch_size=batch_size, now=None)
    return sum(
        cleanup_terminal_private_data(layout, repository, public_id)
        for public_id in repository.terminal_private_cleanup_batch(batch_size)
    )


def expire_job_metadata(
    repository: ArtifactRepository,
    *,
    batch_size: int,
    now: datetime | None = None,
) -> int:
    _validate_cleanup_arguments(batch_size=batch_size, now=now)
    current = now or datetime.now(UTC)
    return repository.expire_jobs(current.isoformat(), batch_size)


def _validate_cleanup_arguments(
    *,
    batch_size: int,
    now: datetime | None,
    grace_seconds: int | None = None,
) -> None:
    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise ValueError("storage cleanup batch is invalid")
    if not 1 <= batch_size <= 10_000:
        raise ValueError("storage cleanup batch is invalid")
    if grace_seconds is not None and (
        not isinstance(grace_seconds, int)
        or isinstance(grace_seconds, bool)
        or not 1 <= grace_seconds <= 31_536_000
    ):
        raise ValueError("orphan grace period is invalid")
    if now is not None and (
        now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now)
    ):
        raise ValueError("storage cleanup time must be UTC")


def _remove_children(path: Path, *, preserved_name: str | None = None) -> None:
    if os.name != "nt":
        _remove_children_posix(path.absolute(), preserved_name=preserved_name)
        return
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _remove_tree(path)
        return
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name == preserved_name:
                try:
                    preserved = os.lstat(entry.path)
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(preserved.st_mode) and not _is_link_or_reparse(
                    preserved
                ):
                    continue
            _remove_tree(Path(entry.path))
    _fsync_directory(path)


def _remove_tree(path: Path) -> None:
    if os.name != "nt":
        _remove_tree_posix(path.absolute())
        return
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not _is_link_or_reparse(info):
        with os.scandir(path) as entries:
            for entry in entries:
                _remove_tree(Path(entry.path))
        path.rmdir()
    else:
        path.unlink()
    _fsync_directory(path.parent)


def _remove_children_posix(path: Path, *, preserved_name: str | None) -> None:
    try:
        parent = _open_directory_chain(path.parent)
    except FileNotFoundError:
        return
    try:
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _remove_entry_at(parent, path.name)
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        directory = os.open(path.name, flags, dir_fd=parent)
        try:
            for name in os.listdir(directory):
                if name == preserved_name:
                    try:
                        preserved = os.stat(
                            name, dir_fd=directory, follow_symlinks=False
                        )
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(preserved.st_mode):
                        continue
                _remove_entry_at(directory, name)
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(parent)


def _remove_tree_posix(path: Path) -> None:
    try:
        parent = _open_directory_chain(path.parent)
    except FileNotFoundError:
        return
    try:
        _remove_entry_at(parent, path.name)
    finally:
        os.close(parent)


def _remove_entry_at(parent: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        directory = os.open(name, flags, dir_fd=parent)
        try:
            for child in os.listdir(directory):
                _remove_entry_at(directory, child)
            os.fsync(directory)
        finally:
            os.close(directory)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)
    os.fsync(parent)


def _open_directory_chain(path: Path) -> int:
    """Open an absolute directory one no-follow component at a time."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
