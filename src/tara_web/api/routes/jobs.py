# ruff: noqa: ANN201, ANN202
"""Protected job snapshots and state-machine commands."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request

from tara_web.api.dependencies.auth import job_owner
from tara_web.api.problem_details import problem
from tara_web.api.schemas import (
    CommandAccepted,
    DeletedResponse,
    JobSnapshot,
    RelaunchSession,
    RotatedSecret,
)
from tara_web.api.snapshots import job_snapshot
from tara_web.db.connection import DatabaseConflict
from tara_web.services.idempotency import IdempotencyConflict
from tara_web.services.upload_sessions import new_opaque_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _owner(request: Request, job_id: str, secret: str | None):
    return job_owner(request, job_id, secret)


@router.get("/{job_id}", response_model=JobSnapshot)
def get_job(
    request: Request,
    job_id: str,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    row = _owner(request, job_id, secret)
    return job_snapshot(request, row) if row else problem(request, 404)


@router.post("/{job_id}/cancel", response_model=CommandAccepted)
def cancel_job(
    request: Request,
    job_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if expected_revision is None or not idempotency_key:
        return problem(request, 428)
    row = _owner(request, job_id, secret)
    if not row:
        return problem(request, 404)
    try:

        def cancel(connection: object) -> dict[str, object]:
            current = connection.execute(
                "SELECT revision FROM jobs WHERE public_id=?", (job_id,)
            ).fetchone()
            if (
                current is None
                or int(current["revision"]) != expected_revision
                or not request.app.state.job_service.request_cancel(
                    job_id, connection=connection
                )
            ):
                raise DatabaseConflict("command unavailable")
            return {"accepted": True}

        result = request.app.state.idempotency.transactional_execute(
            "cancel_job",
            job_id,
            idempotency_key,
            {"expected_revision": expected_revision},
            cancel,
        )
        # Never signal the process pool before the idempotent mutation commits.
        request.app.state.job_scheduler.pool.cancel(job_id)
        request.app.state.job_scheduler.wakeup()
        request.app.state.publish_job_event(job_id, expected_revision + 1)
        return result
    except (DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)


@router.post("/{job_id}/secret", response_model=RotatedSecret)
def regenerate_secret(
    request: Request,
    job_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if expected_revision is None or not idempotency_key:
        return problem(request, 428)
    row = _owner(request, job_id, secret)
    replay = bool(
        secret
        and idempotency_key
        and request.app.state.idempotency.rotation_replay_authorized(
            owner=job_id, key=idempotency_key, secret=secret
        )
    )
    if not row and not replay:
        return problem(request, 404)
    try:
        replacement = request.app.state.secret_hmac.derive_secret(
            f"{job_id}:{idempotency_key}", "job-secret-rotation"
        )

        def rotate(connection: object) -> dict[str, object]:
            cursor = connection.execute(
                "UPDATE jobs SET secret_hmac=?,secret_generation="
                "secret_generation+1,revision=revision+1,updated_at="
                "datetime('now') WHERE public_id=? AND revision=? "
                "AND status NOT IN ('expired','deleted')",
                (
                    request.app.state.secret_hmac.digest(replacement, "upload-secret"),
                    job_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("resource revision conflict")
            connection.execute(
                "UPDATE upload_sessions SET secret_hmac=?,secret_generation="
                "secret_generation+1,revision=revision+1,updated_at="
                "datetime('now') WHERE id=?",
                (
                    request.app.state.secret_hmac.digest(replacement, "upload-secret"),
                    row["upload_session_id"],
                ),
            )
            return {
                "revision": expected_revision + 1,
                "authorization_proof": request.app.state.secret_hmac.digest(
                    secret or "", "rotation-replay"
                ),
            }

        result = request.app.state.idempotency.transactional_execute(
            "rotate_job_secret",
            job_id,
            idempotency_key,
            {"expected_revision": expected_revision},
            rotate,
        )
        request.app.state.publish_job_event(job_id, int(result["revision"]))
        # The secret is deterministic for this job/key and never enters result_json.
        return {"secret": replacement, "revision": result["revision"]}
    except (DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)


@router.post("/{job_id}/relaunch-identical", response_model=CommandAccepted)
def relaunch_identical(
    request: Request,
    job_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if expected_revision is None or not idempotency_key:
        return problem(request, 428)
    if _owner(request, job_id, secret) is None:
        return problem(request, 404)
    try:

        def relaunch(connection: object) -> dict[str, object]:
            new_job_id = new_opaque_id("job")
            new_session_id = new_opaque_id("us")
            request.app.state.budget_service.reserve(
                new_job_id,
                1,
                connection=connection,
            )
            return request.app.state.relaunch_service.create_identical(
                connection,
                source_public_id=job_id,
                expected_revision=expected_revision,
                job_public_id=new_job_id,
                session_public_id=new_session_id,
                session_retention_hours=(
                    request.app.state.runtime_config.web.limits.upload_session_retention_hours
                ),
            )

        result = request.app.state.idempotency.transactional_execute(
            "relaunch_identical",
            job_id,
            idempotency_key,
            {"expected_revision": expected_revision},
            relaunch,
        )
        request.app.state.job_scheduler.wakeup()
        request.app.state.publish_job_event(job_id, expected_revision + 1)
        request.app.state.publish_job_event(str(result["job_id"]), 1)
        return result
    except (DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)


@router.post(
    "/{job_id}/edit-and-relaunch", status_code=201, response_model=RelaunchSession
)
def edit_and_relaunch(
    request: Request,
    job_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if expected_revision is None or not idempotency_key or not secret:
        return problem(request, 428)
    row = _owner(request, job_id, secret)
    if row is None:
        return problem(request, 404)
    try:

        def create(connection: object) -> dict[str, object]:
            current = connection.execute(
                "SELECT revision,status FROM jobs WHERE public_id=?", (job_id,)
            ).fetchone()
            if current is None or int(current["revision"]) != expected_revision:
                raise DatabaseConflict("resource revision conflict")
            session_id = new_opaque_id("us")
            return request.app.state.relaunch_service.create_editable(
                connection,
                source_public_id=job_id,
                expected_revision=expected_revision,
                session_public_id=session_id,
                secret_hmac=request.app.state.secret_hmac.digest(
                    secret, "upload-secret"
                ),
                retention_hours=request.app.state.runtime_config.web.limits.upload_session_retention_hours,
            )

        return request.app.state.idempotency.transactional_execute(
            "edit_and_relaunch",
            job_id,
            idempotency_key,
            {"expected_revision": expected_revision},
            create,
        )
    except (DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)


@router.delete("/{job_id}", response_model=DeletedResponse)
def delete_job(
    request: Request,
    job_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    if expected_revision is None or not idempotency_key:
        return problem(request, 428)
    row = _owner(request, job_id, secret)
    if not row:
        return problem(request, 404)
    try:

        def delete(connection: object) -> dict[str, object]:
            cursor = connection.execute(
                "UPDATE jobs SET status='deleted',revision=revision+1,"
                "updated_at=datetime('now') WHERE public_id=? AND revision=? "
                "AND status IN ('completed','failed','timed_out','cancelled',"
                "'cancel_failed','expired')",
                (job_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise DatabaseConflict("command unavailable")
            return {"deleted": True}

        return request.app.state.idempotency.transactional_execute(
            "delete_job",
            job_id,
            idempotency_key,
            {"expected_revision": expected_revision},
            delete,
        )
    except (DatabaseConflict, IdempotencyConflict):
        return problem(request, 409)
