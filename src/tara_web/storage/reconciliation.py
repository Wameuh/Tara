"""Idempotent convergence of interrupted staged writes and deletions."""

import hashlib
from collections.abc import Callable

from tara_web.db.connection import DatabaseConflict
from tara_web.db.repositories.artifacts import ArtifactRepository

from .atomic import promote_staged, read_regular, unlink_regular, unlink_temporary
from .cleanup import delete_artifact
from .layout import StorageError, StorageLayout


def reconcile(
    layout: StorageLayout,
    repository: ArtifactRepository,
    *,
    limit: int = 100,
    max_bytes: int = 1_073_741_824,
    validator: Callable[[bytes], None] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[int, int]:
    selected = transitioned = 0
    for row in repository.reconciliation_batch(limit):
        selected += 1
        artifact_id = int(row["id"])
        destination = None
        if row["storage_state"] == "deleting":
            if delete_artifact(
                layout, repository, artifact_id, sleeper=sleeper
            ).success:
                transitioned += 1
            continue
        try:
            destination = layout.parse_artifact_path(str(row["relative_path"]))
            if row["storage_state"] == "pending":
                _remove_pending(layout, destination)
                repository.mark_error(artifact_id, "write_failed")
                transitioned += 1
                continue
            try:
                content = read_regular(
                    layout,
                    destination,
                    expected_bytes=int(row["byte_size"]),
                    max_bytes=max_bytes,
                )
            except StorageError:
                content = b""
            temporary = row["staged_temp_name"]
            if temporary and not content:
                try:
                    promote_staged(layout, destination, str(temporary))
                except StorageError:
                    pass
            content = read_regular(
                layout,
                destination,
                expected_bytes=int(row["byte_size"]),
                max_bytes=max_bytes,
            )
            if len(content) != int(row["byte_size"]):
                raise StorageError("artifact integrity failed")
            expected = row["sha256_hex"]
            if expected is not None and hashlib.sha256(content).hexdigest() != expected:
                raise StorageError("artifact integrity failed")
            if row["artifact_type"] == "final_yaml":
                if validator is None:
                    raise StorageError("artifact validation unavailable")
                validator(content)
            repository.mark_ready(artifact_id)
            transitioned += 1
        except (DatabaseConflict, StorageError, TypeError, ValueError):
            _remove_pending(layout, destination, row.get("staged_temp_name"))
            try:
                repository.mark_error(artifact_id, "integrity_failed")
                transitioned += 1
            except DatabaseConflict:
                pass
    return selected, transitioned


def reconcile_all(
    layout: StorageLayout,
    repository: ArtifactRepository,
    *,
    limit: int,
    max_bytes: int,
    validator: Callable[[bytes], None] | None,
) -> None:
    """Drain bounded batches; each successful mutation leaves the selection."""
    while True:
        selected, transitioned = reconcile(
            layout,
            repository,
            limit=limit,
            max_bytes=max_bytes,
            validator=validator,
        )
        if selected == 0:
            return
        if transitioned == 0:
            raise StorageError("storage reconciliation stalled")


def _remove_pending(
    layout: StorageLayout, destination: object, temporary: object = None
) -> None:
    if destination is None:
        return
    try:
        unlink_regular(layout, destination)  # type: ignore[arg-type]
    except StorageError:
        pass
    if isinstance(temporary, str):
        try:
            unlink_temporary(layout, destination, temporary)  # type: ignore[arg-type]
        except StorageError:
            pass
