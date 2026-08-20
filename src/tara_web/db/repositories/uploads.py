"""Upload session persistence and bounded capacity reservations."""

from __future__ import annotations

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.domain.models import UploadSession, utc_now


class UploadRepository:
    def __init__(self, factory: ConnectionFactory) -> None:
        self._factory = factory

    def reserve(
        self,
        session: UploadSession,
        *,
        max_active_jobs: int,
        max_reserved_bytes: int,
    ) -> None:
        """Reserve one slot with a short immediate transaction; never over-allocates."""
        if max_active_jobs < 1 or max_reserved_bytes < 0:
            raise ValueError("capacity limits are invalid")
        if session.reserved_bytes > max_reserved_bytes:
            raise DatabaseConflict("capacity is unavailable")
        with self._factory.transaction() as connection:
            active = connection.execute(
                "SELECT (SELECT COUNT(*) FROM upload_sessions WHERE status IN "
                "('created','uploading','validating','ready',"
                "'waiting_for_capacity')) + "
                "(SELECT COUNT(*) FROM jobs WHERE status IN "
                "('queued','running','cancel_requested','stopping'))"
            ).fetchone()[0]
            if active >= max_active_jobs:
                raise DatabaseConflict("capacity is unavailable")
            reserved = connection.execute(
                "SELECT COALESCE(SUM(reserved_bytes), 0) FROM upload_sessions "
                "WHERE status IN ('created','uploading','validating','ready',"
                "'waiting_for_capacity')"
            ).fetchone()[0]
            if int(reserved) + session.reserved_bytes > max_reserved_bytes:
                raise DatabaseConflict("capacity is unavailable")
            timestamp = utc_now()
            connection.execute(
                "INSERT INTO upload_sessions (public_id, secret_hmac, "
                "secret_generation, status, reserved_bytes, expires_at, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.public_id,
                    session.secret_hmac,
                    session.secret_generation,
                    session.status,
                    session.reserved_bytes,
                    session.expires_at,
                    timestamp,
                    timestamp,
                ),
            )

    def compare_and_set_status(
        self, public_id: str, expected_revision: int, status: str
    ) -> int:
        with self._factory.transaction() as connection:
            cursor = connection.execute(
                "UPDATE upload_sessions SET status = ?, revision = revision + 1, "
                "updated_at = ? WHERE public_id = ? AND revision = ?",
                (status, utc_now(), public_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("resource revision conflict")
            return int(
                connection.execute(
                    "SELECT revision FROM upload_sessions WHERE public_id = ?",
                    (public_id,),
                ).fetchone()[0]
            )

    def advance_offset(
        self, file_id: str, expected_offset: int, byte_count: int, digest: str
    ) -> int:
        """Record a persisted chunk only when the caller owns the confirmed offset."""
        if byte_count <= 0 or len(digest) != 64:
            raise ValueError("invalid chunk")
        with self._factory.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM upload_files WHERE public_id = ? "
                "AND confirmed_offset = ?",
                (file_id, expected_offset),
            ).fetchone()
            if row is None:
                raise DatabaseConflict("upload offset conflict")
            timestamp = utc_now()
            connection.execute(
                "INSERT INTO upload_chunks (file_id, offset_bytes, byte_count, "
                "sha256_hex, created_at) VALUES (?, ?, ?, ?, ?)",
                (row[0], expected_offset, byte_count, digest, timestamp),
            )
            cursor = connection.execute(
                "UPDATE upload_files SET confirmed_offset = confirmed_offset + ?, "
                "revision = revision + 1, updated_at = ? WHERE id = ? "
                "AND confirmed_offset = ?",
                (byte_count, timestamp, row[0], expected_offset),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("upload offset conflict")
            return expected_offset + byte_count
