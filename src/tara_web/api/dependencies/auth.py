"""Owner-secret authentication without URL credentials."""

from __future__ import annotations

from fastapi import Header, Request

from tara_web.services.upload_sessions import UploadUnauthorized


class InvalidSecretRateLimited(RuntimeError):
    pass


def _failed(request: Request) -> None:
    retry = request.app.state.rate_limiter.allow(
        "invalid_secret", request.state.client_identity
    )
    if retry is not None:
        raise InvalidSecretRateLimited(retry)


def session_owner(
    request: Request, session_id: str, secret: str | None
) -> dict[str, object]:
    if not secret or len(secret) > 512:
        _failed(request)
        raise UploadUnauthorized("resource unavailable")
    try:
        return request.app.state.upload_sessions.authorize(session_id, secret)
    except UploadUnauthorized:
        _failed(request)
        raise


def readable_session_owner(
    request: Request, session_id: str, secret: str | None
) -> dict[str, object] | None:
    """Keep a consumed session as a protected, read-only job hand-off."""
    if not secret or len(secret) > 512:
        _failed(request)
        return None
    row = request.app.state.audio_upload_repository.session(session_id)
    if row is None or not request.app.state.secret_hmac.verify(
        secret, str(row["secret_hmac"]), "upload-secret"
    ):
        _failed(request)
        return None
    return row


def job_owner(
    request: Request, job_id: str, secret: str | None
) -> dict[str, object] | None:
    if not secret or len(secret) > 512:
        _failed(request)
        return None
    database = request.app.state.database
    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE public_id=?", (job_id,)
        ).fetchone()
        if row is None or not request.app.state.secret_hmac.verify(
            secret, str(row["secret_hmac"]), "upload-secret"
        ):
            _failed(request)
            return None
        return dict(row)
    finally:
        connection.close()


def current_job_owner(
    request: Request, job_id: str, secret: str | None
) -> dict[str, object] | None:
    """Revalidate an already accepted stream without charging failed-auth limits."""
    if not secret or len(secret) > 512:
        return None
    connection = request.app.state.database.connect()
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE public_id=?", (job_id,)
        ).fetchone()
        if row is None or not request.app.state.secret_hmac.verify(
            secret, str(row["secret_hmac"]), "upload-secret"
        ):
            return None
        return dict(row)
    finally:
        connection.close()


def secret_header(x_tara_job_secret: str | None = Header(default=None)) -> str | None:
    # Headers are bounded by the ASGI server; reject pathological browser input here.
    return (
        x_tara_job_secret
        if x_tara_job_secret and len(x_tara_job_secret) <= 512
        else None
    )
