"""Bounded crash reconciliation and expiry cleanup for managed upload files."""

from __future__ import annotations

from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.storage.layout import StorageError, StorageLayout
from tara_web.storage.uploads import reconcile_existing, unlink_upload


def maintain_uploads(
    repository: AudioUploadRepository, layout: StorageLayout, *, batch_size: int
) -> int:
    """Expire sessions and clean at most one bounded batch of tombstones."""
    work = repository.expire_sessions(limit=batch_size)
    work += repository.scrub_terminal_session_text(limit=batch_size)
    work += repository.purge_expired_empty_sessions(limit=batch_size)
    for row in repository.maintenance_files(limit=batch_size):
        work += 1
        path = str(row["storage_path"])
        if _unlink(layout, path):
            repository.mark_storage_cleaned(str(row["public_id"]))
    return work


def drain_startup_uploads(
    repository: AudioUploadRepository, layout: StorageLayout, *, batch_size: int
) -> int:
    """Reconcile all live uploads and drain terminal tombstones before readiness."""
    if batch_size < 1:
        raise ValueError("upload maintenance batch size is invalid")
    work = 0
    while repository.expire_sessions(limit=batch_size) == batch_size:
        work += batch_size
    while scrubbed := repository.scrub_terminal_session_text(limit=batch_size):
        work += scrubbed
        if scrubbed < batch_size:
            break
    while purged := repository.purge_expired_empty_sessions(limit=batch_size):
        work += purged
        if purged < batch_size:
            break
    after_id = 0
    while rows := repository.startup_nonterminal_files(
        after_id=after_id, limit=batch_size
    ):
        for row in rows:
            work += 1
            _reconcile_row(repository, layout, row)
        after_id = int(rows[-1]["id"])
    tombstone_after_id = 0
    while True:
        rows = repository.maintenance_files(
            after_id=tombstone_after_id, limit=batch_size
        )
        if not rows:
            break
        for row in rows:
            work += 1
            path = str(row["storage_path"])
            if _unlink(layout, path):
                repository.mark_storage_cleaned(str(row["public_id"]))
        tombstone_after_id = int(rows[-1]["id"])
    return work


def _reconcile_row(
    repository: AudioUploadRepository, layout: StorageLayout, row: dict[str, object]
) -> None:
    try:
        upload = layout.parse_upload_path(str(row["storage_path"]))
        reconcile_existing(layout, upload, int(row["confirmed_offset"]))
    except (FileNotFoundError, StorageError, OSError):
        if int(row["confirmed_offset"]) > 0:
            repository.mark_integrity_invalid(str(row["public_id"]))


def _unlink(layout: StorageLayout, relative_path: str) -> bool:
    try:
        _ = unlink_upload(layout, layout.parse_upload_path(relative_path))
        return True
    except (StorageError, OSError):
        return False
