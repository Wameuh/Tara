# ruff: noqa: ANN201
"""Canonical session snapshots and promotion into a job."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Request

from tara_web.api.dependencies.auth import readable_session_owner, session_owner
from tara_web.api.problem_details import problem
from tara_web.api.schemas import CreatedJob, SessionInputsUpdate, SessionSnapshot
from tara_web.api.snapshots import session_snapshot
from tara_web.db.connection import DatabaseConflict
from tara_web.db.repositories.jobs import JobRepository
from tara_web.domain.models import Job
from tara_web.services.idempotency import IdempotencyConflict
from tara_web.services.upload_sessions import UploadUnauthorized, new_opaque_id

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.patch("/{session_id}/inputs", response_model=SessionSnapshot)
def update_inputs(
    request: Request,
    session_id: str,
    body: SessionInputsUpdate,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    """Persist editable inputs; this is deliberately separate from legacy uploads."""
    if expected_revision is None or not idempotency_key:
        return problem(request, 428)
    if session_owner(request, session_id, secret) is None:
        return problem(request, 404 if expected_revision is not None else 428)
    if body.language not in request.app.state.runtime_config.web.supported_languages:
        return problem(request, 422)
    try:

        def update(connection: object) -> dict[str, object]:
            cursor = connection.execute(
                "UPDATE upload_sessions SET language=?,context_text=?,"
                "previous_summaries_text=?,revision=revision+1,"
                "updated_at=datetime('now') WHERE public_id=? AND revision=? "
                "AND status IN ('created','uploading','validating','ready',"
                "'waiting_for_capacity')",
                (
                    body.language,
                    body.context_text,
                    body.previous_summaries_text,
                    session_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("session inputs are unavailable")
            return {"revision": expected_revision + 1}

        request.app.state.idempotency.transactional_execute(
            "update_session_inputs",
            session_id,
            idempotency_key,
            {
                "revision": expected_revision,
                "language": body.language,
                "context_sha256": hashlib.sha256(
                    body.context_text.encode()
                ).hexdigest(),
                "previous_summaries_sha256": hashlib.sha256(
                    body.previous_summaries_text.encode()
                ).hexdigest(),
            },
            update,
        )
    except (DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)
    snapshot = session_snapshot(request, session_id)
    return snapshot if snapshot is not None else problem(request, 404)


@router.get("/{session_id}", response_model=SessionSnapshot)
def get_session(
    request: Request,
    session_id: str,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if readable_session_owner(request, session_id, secret) is None:
        return problem(request, 404)
    snapshot = session_snapshot(request, session_id)
    return snapshot if snapshot is not None else problem(request, 404)


@router.post("/{session_id}/jobs", status_code=201, response_model=CreatedJob)
def launch_job(
    request: Request,
    session_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if request.app.state.draining:
        return problem(request, 503)
    if expected_revision is None or not idempotency_key:
        return problem(request, 428)
    try:
        owner = session_owner(request, session_id, secret)

        def create(connection: object) -> dict[str, object]:
            job_id = new_opaque_id("job")
            request.app.state.budget_service.reserve(
                job_id, 1, connection=connection
            )
            JobRepository(request.app.state.database).promote(
                Job(
                    job_id,
                    session_id,
                    str(owner["secret_hmac"]),
                    "v1",
                    (datetime.now(UTC) + timedelta(days=7)).isoformat(),
                    job_type=str(owner["input_type"]),
                    language=str(owner["language"]),
                ),
                expected_session_revision=expected_revision,
                max_waiting_jobs=request.app.state.runtime_config.web.limits.max_waiting_jobs,
                connection=connection,
            )
            return {"job_id": job_id, "revision": 1}

        result = request.app.state.idempotency.transactional_execute(
            "launch_job",
            session_id,
            idempotency_key,
            {"session_id": session_id, "expected_revision": expected_revision},
            create,
        )
        # The durable queue row exists before this in-memory wake-up.
        request.app.state.job_scheduler.wakeup()
        request.app.state.publish_job_event(result["job_id"], int(result["revision"]))
        return result
    except UploadUnauthorized:
        return problem(request, 404)
    except (ValueError, DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)
