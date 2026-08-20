"""Bounded artifact retention cleanup with a stateful no-follow scanner."""

from __future__ import annotations

import os
import re
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
    """Remove terminal job inputs no later than 24 hours after upload."""
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
