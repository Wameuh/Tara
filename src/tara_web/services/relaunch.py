"""Crash-safe creation of a distinct, single-use empty relaunch session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tara_web.db.connection import DatabaseConflict
from tara_web.storage.layout import StorageLayout


class RelaunchService:
    def __init__(self, layout: StorageLayout) -> None:
        self._layout = layout

    def create_identical(
        self,
        connection: object,
        *,
        source_public_id: str,
        expected_revision: int,
        job_public_id: str,
        session_public_id: str,
        session_retention_hours: int = 24,
    ) -> dict[str, object]:
        raise DatabaseConflict("identical relaunch is unavailable")

    def create_editable(
        self,
        connection: object,
        *,
        source_public_id: str,
        expected_revision: int,
        session_public_id: str,
        secret_hmac: str,
        admission_identity_hmac: str,
        max_sessions: int,
        max_sessions_per_identity: int,
        max_pending_sessions: int,
        max_pending_sessions_per_identity: int,
        max_reserved_bytes: int,
        pending_ttl_seconds: int,
        retention_hours: int = 24,
    ) -> dict[str, object]:
        """Create a fresh empty session without copying terminal private data."""
        source = connection.execute(
            "SELECT j.* FROM jobs j WHERE j.public_id=? AND j.revision=? "
            "AND j.status IN "
            "('failed','timed_out','cancelled','cancel_failed') "
            "AND j.editable_relaunch_session_id IS NULL "
            "AND julianday(j.expires_at)>julianday('now')",
            (source_public_id, expected_revision),
        ).fetchone()
        if source is None:
            raise DatabaseConflict("editable relaunch is unavailable")
        now = datetime.now(UTC)
        live = now.isoformat()
        pending = connection.execute(
            "SELECT COUNT(*) FROM upload_sessions WHERE status='created' "
            "AND julianday(expires_at)>julianday(?)",
            (live,),
        ).fetchone()[0]
        per_identity = connection.execute(
            "SELECT COUNT(*) FROM upload_sessions WHERE status='created' "
            "AND admission_identity_hmac=? "
            "AND julianday(expires_at)>julianday(?)",
            (admission_identity_hmac, live),
        ).fetchone()[0]
        if (
            pending >= max_pending_sessions
            or per_identity >= max_pending_sessions_per_identity
        ):
            raise DatabaseConflict("editable relaunch pending capacity unavailable")
        expires_at = now + timedelta(seconds=pending_ttl_seconds)
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at,last_activity_at,relaunch_parent_job_id,language,"
            "context_text,previous_summaries_text,input_type,reserved_bytes,"
            "admission_identity_hmac) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_public_id,
                secret_hmac,
                "created",
                expires_at.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                source["id"],
                source["language"],
                "",
                "",
                source["job_type"],
                0,
                admission_identity_hmac,
            ),
        )
        session_id = connection.execute(
            "SELECT id FROM upload_sessions WHERE public_id=?", (session_public_id,)
        ).fetchone()[0]
        claimed = connection.execute(
            "UPDATE jobs SET editable_relaunch_session_id=?,revision=revision+1,"
            "updated_at=? WHERE id=? AND revision=? "
            "AND editable_relaunch_session_id IS NULL",
            (session_id, live, source["id"], expected_revision),
        )
        if claimed.rowcount != 1:
            raise DatabaseConflict("editable relaunch is unavailable")
        return {"session_id": session_public_id, "revision": 1}
