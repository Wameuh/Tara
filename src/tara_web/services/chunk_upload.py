"""Per-file locked, fsync-before-CAS chunk ingestion."""

from __future__ import annotations

import hashlib
import hmac
import threading
from dataclasses import dataclass

from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.storage.layout import StorageError, StorageLayout
from tara_web.storage.uploads import reconcile_length, write_chunk

from .upload_sessions import UploadSessionService


@dataclass
class _LockEntry:
    lock: threading.Lock
    users: int = 0


class ChunkUploadService:
    def __init__(
        self,
        sessions: UploadSessionService,
        repository: AudioUploadRepository,
        layout: StorageLayout,
        *,
        max_chunk_bytes: int,
        minimum_free_bytes: int = 0,
    ) -> None:
        self.sessions, self.repository, self.layout = sessions, repository, layout
        self.max_chunk_bytes = max_chunk_bytes
        self.minimum_free_bytes = minimum_free_bytes
        self._locks: dict[str, _LockEntry] = {}
        self._locks_guard = threading.Lock()

    def _acquire_lock(self, file_id: str) -> _LockEntry:
        with self._locks_guard:
            entry = self._locks.setdefault(file_id, _LockEntry(threading.Lock()))
            entry.users += 1
            return entry

    def _release_lock(self, file_id: str, entry: _LockEntry) -> None:
        with self._locks_guard:
            entry.users -= 1
            if entry.users == 0 and self._locks.get(file_id) is entry:
                del self._locks[file_id]

    def offset(self, session_id: str, file_id: str, secret: str | None) -> int:
        self.sessions.authorize(session_id, secret, mutable=True)
        row = self.repository.file_for_session(session_id, file_id)
        if row is None or row["status"] not in {"created", "uploading"}:
            raise StorageError("upload resource unavailable")
        upload = self.layout.parse_upload_path(str(row["storage_path"]))
        reconcile_length(self.layout, upload, int(row["confirmed_offset"]))
        return int(row["confirmed_offset"])

    def write(
        self,
        session_id: str,
        file_id: str,
        secret: str | None,
        *,
        offset: int,
        digest: str,
        content: bytes,
    ) -> int:
        if (
            offset < 0
            or not content
            or len(content) > self.max_chunk_bytes
            or not isinstance(digest, str)
            or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), digest)
        ):
            raise ValueError("upload chunk is invalid")
        self.sessions.authorize(session_id, secret, mutable=True)
        entry = self._acquire_lock(file_id)
        try:
            with entry.lock:
                row = self.repository.file_for_session(session_id, file_id)
                if (
                    row is None
                    or row["status"] not in {"created", "uploading"}
                    or offset + len(content) > int(row["declared_bytes"])
                ):
                    raise StorageError("upload offset conflict")
                confirmed = int(row["confirmed_offset"])
                if confirmed != offset:
                    # Lost response: accept only the already durable, identical chunk.
                    if confirmed == offset + len(
                        content
                    ) and self.repository.chunk_matches(
                        file_id, offset, len(content), digest
                    ):
                        return confirmed
                    raise StorageError("upload offset conflict")
                upload = self.layout.parse_upload_path(str(row["storage_path"]))
                reconcile_length(self.layout, upload, offset)
                write_chunk(
                    self.layout,
                    upload,
                    offset,
                    content,
                    minimum_free_bytes=self.minimum_free_bytes,
                )
                return self.repository.advance(file_id, offset, len(content), digest)
        finally:
            self._release_lock(file_id, entry)
