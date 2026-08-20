"""Strict HTTP surface for direct resumable MP3/OGG uploads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tara_web.db.connection import DatabaseConflict, DatabaseError
from tara_web.services.idempotency import IdempotencyConflict
from tara_web.services.upload_sessions import (
    UploadUnauthorized,
    new_opaque_id,
    sanitize_person,
)
from tara_web.storage.layout import StorageError
from tara_web.storage.uploads import unlink_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileDeclaration(Body):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    size: Annotated[int, Field(gt=0)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime: Annotated[str | None, Field(default=None, max_length=128)]


class PersonChange(Body):
    person: Annotated[str, Field(min_length=1, max_length=255)]


class ReplacementDeclaration(FileDeclaration):
    replaces_file_id: Annotated[str, Field(min_length=16, max_length=128)]


def _problem(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": code, "status": status, "code": code},
        status_code=status,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


def _idempotent(
    request: Request,
    operation: str,
    owner: str,
    key: str,
    payload: dict[str, object],
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    service = request.app.state.upload_sessions.idempotency
    if service is None:
        return action()
    return service.transactional_execute(operation, owner, key, payload, action)


def _queue_validation(
    request: Request, session_id: str, file_id: str, expected_revision: int
) -> dict[str, object]:
    validation_id = new_opaque_id("uv")
    request.app.state.audio_upload_repository.queue_validation(
        session_id, file_id, expected_revision, validation_id
    )
    return {
        "validation_id": validation_id,
        "status": "queued",
        "revision": expected_revision + 1,
    }


def _cleanup_file(request: Request, session_id: str, file_id: str) -> None:
    row = request.app.state.audio_upload_repository.file_for_session(
        session_id, file_id
    )
    if row is None:
        return
    try:
        unlink_upload(
            request.app.state.storage_layout,
            request.app.state.storage_layout.parse_upload_path(
                str(row["storage_path"])
            ),
        )
    except StorageError:
        pass


@router.post("/sessions", status_code=201, response_model=None)
def create_session(
    request: Request,
    input_type: Literal["audio", "merged_transcription", "zip"] = "audio",
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, object]:
    if not idempotency_key:
        return _problem(400, "idempotency_key_required")
    try:
        created = request.app.state.upload_sessions.create(
            idempotency_key, input_type=input_type
        )
    except (ValueError, DatabaseConflict, IdempotencyConflict):
        return _problem(409, "idempotency_conflict")
    return {"session_id": created.session_id, "secret": created.secret, "revision": 1}


@router.get("/sessions/{session_id}", response_model=None)
def session_snapshot(
    request: Request,
    session_id: str,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
) -> Response | dict[str, object]:
    try:
        row = request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        maximum = request.app.state.runtime_config.web.limits.max_upload_files
        files = request.app.state.audio_upload_repository.files_for_session(
            session_id, limit=maximum
        )
        return {
            "session_id": session_id,
            "input_type": "zip" if row["archive_mode"] else row["input_type"],
            "status": row["status"],
            "revision": row["revision"],
            "expires_at": row["expires_at"],
            "files": [
                {
                    "file_id": item["public_id"],
                    "status": item["status"],
                    "revision": item["revision"],
                    "confirmed_offset": item["confirmed_offset"],
                    "declared_bytes": item["declared_bytes"],
                    "person": item["person"],
                    "warning_code": item["validation_warning_code"],
                    "error_code": item["validation_error_code"],
                    "error_path": item["validation_error_path"],
                    "schema_name": item["schema_name"],
                    "schema_version": item["schema_version"],
                    "token_count": item["token_count"],
                }
                for item in files
            ],
        }
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")


@router.post("/sessions/{session_id}/files", status_code=201, response_model=None)
def declare_file(
    request: Request,
    session_id: str,
    body: FileDeclaration,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, object]:
    if not idempotency_key:
        return _problem(400, "idempotency_key_required")
    try:
        request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        result = _idempotent(
            request,
            "declare_upload_file",
            session_id,
            idempotency_key,
            {"session_id": session_id, **body.model_dump()},
            lambda: request.app.state.upload_sessions.declare_file(
                session_id,
                x_tara_job_secret,
                filename=body.filename,
                size=body.size,
                sha256_hex=body.sha256,
                mime=body.mime,
            ),
        )
        return result
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (ValueError, DatabaseConflict, IdempotencyConflict):
        return _problem(409, "upload_declaration_invalid")


@router.post(
    "/sessions/{session_id}/files/replace",
    status_code=201,
    response_model=None,
)
def replace_file(
    request: Request,
    session_id: str,
    body: ReplacementDeclaration,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, object]:
    if not idempotency_key:
        return _problem(400, "idempotency_key_required")
    try:
        request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        result = _idempotent(
            request,
            "replace_upload_file",
            session_id,
            idempotency_key,
            {"session_id": session_id, **body.model_dump()},
            lambda: request.app.state.upload_sessions.declare_file(
                session_id,
                x_tara_job_secret,
                filename=body.filename,
                size=body.size,
                sha256_hex=body.sha256,
                mime=body.mime,
                replacement_for=body.replaces_file_id,
            ),
        )
        return result
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (ValueError, DatabaseConflict, IdempotencyConflict):
        return _problem(409, "upload_declaration_invalid")


@router.get("/sessions/{session_id}/files/{file_id}/offset", response_model=None)
def file_offset(
    request: Request,
    session_id: str,
    file_id: str,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
) -> Response | dict[str, int]:
    try:
        confirmed_offset = request.app.state.chunk_upload.offset(
            session_id, file_id, x_tara_job_secret
        )
        row = request.app.state.audio_upload_repository.file_for_session(
            session_id, file_id
        )
        if row is None:
            return _problem(404, "upload_resource_unavailable")
        return {"confirmed_offset": confirmed_offset, "revision": int(row["revision"])}
    except (UploadUnauthorized, StorageError, DatabaseError):
        return _problem(404, "upload_resource_unavailable")


@router.patch("/sessions/{session_id}/files/{file_id}/chunks", response_model=None)
async def upload_chunk(
    request: Request,
    session_id: str,
    file_id: str,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    upload_offset: Annotated[int | None, Header()] = None,
    upload_checksum: Annotated[str | None, Header()] = None,
) -> Response | dict[str, int]:
    if upload_offset is None or upload_checksum is None:
        return _problem(400, "upload_chunk_headers_required")
    maximum = request.app.state.runtime_config.web.limits.max_chunk_bytes
    content = bytearray()
    try:
        async with asyncio.timeout(
            request.app.state.runtime_config.web.limits.upload_read_timeout_seconds
        ):
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > maximum:
                    return _problem(413, "upload_chunk_too_large")
    except TimeoutError:
        return _problem(408, "upload_chunk_timeout")
    try:
        offset = await asyncio.to_thread(
            request.app.state.chunk_upload.write,
            session_id,
            file_id,
            x_tara_job_secret,
            offset=upload_offset,
            digest=upload_checksum,
            content=bytes(content),
        )
        row = request.app.state.audio_upload_repository.file_for_session(
            session_id, file_id
        )
        if row is None:
            return _problem(404, "upload_resource_unavailable")
        return {"confirmed_offset": offset, "revision": int(row["revision"])}
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (ValueError, StorageError, DatabaseError):
        return _problem(409, "upload_chunk_conflict")


@router.patch("/sessions/{session_id}/files/{file_id}/person", response_model=None)
def change_person(
    request: Request,
    session_id: str,
    file_id: str,
    body: PersonChange,
    expected_revision: Annotated[int | None, Header()] = None,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
) -> Response | dict[str, int]:
    if expected_revision is None:
        return _problem(428, "expected_revision_required")
    try:
        request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        revision = request.app.state.audio_upload_repository.set_person(
            session_id, file_id, expected_revision, sanitize_person(body.person)
        )
        return {"revision": revision}
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (ValueError, DatabaseConflict):
        return _problem(409, "resource_revision_conflict")


@router.post(
    "/sessions/{session_id}/files/{file_id}/finalize",
    status_code=202,
    response_model=None,
)
def finalize_file(
    request: Request,
    session_id: str,
    file_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, object]:
    if expected_revision is None or not idempotency_key:
        return _problem(428, "mutation_precondition_required")
    try:
        request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        result = _idempotent(
            request,
            "finalize_upload_file",
            session_id,
            idempotency_key,
            {
                "session_id": session_id,
                "file_id": file_id,
                "expected_revision": expected_revision,
            },
            lambda: _queue_validation(request, session_id, file_id, expected_revision),
        )
        request.app.state.loop.call_soon_threadsafe(
            request.app.state.upload_validation_wakeup.set
        )
        return result
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (DatabaseConflict, IdempotencyConflict):
        return _problem(409, "resource_revision_conflict")


@router.post(
    "/sessions/{session_id}/files/{file_id}/retry",
    status_code=202,
    response_model=None,
)
def retry_file(
    request: Request,
    session_id: str,
    file_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, object]:
    return finalize_file(
        request,
        session_id,
        file_id,
        expected_revision,
        x_tara_job_secret,
        idempotency_key,
    )


@router.delete("/sessions/{session_id}/files/{file_id}", response_model=None)
def delete_file(
    request: Request,
    session_id: str,
    file_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, int]:
    if expected_revision is None or not idempotency_key:
        return _problem(428, "mutation_precondition_required")
    try:
        request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        result = _idempotent(
            request,
            "delete_upload_file",
            session_id,
            idempotency_key,
            {
                "session_id": session_id,
                "file_id": file_id,
                "expected_revision": expected_revision,
            },
            lambda: {
                "revision": request.app.state.audio_upload_repository.delete_file(
                    session_id, file_id, expected_revision
                )
            },
        )
        _cleanup_file(request, session_id, file_id)
        return result
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (DatabaseConflict, IdempotencyConflict):
        return _problem(409, "resource_revision_conflict")


@router.post("/sessions/{session_id}/cancel", response_model=None)
def cancel_session(
    request: Request,
    session_id: str,
    expected_revision: Annotated[int | None, Header()] = None,
    x_tara_job_secret: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response | dict[str, int]:
    if expected_revision is None or not idempotency_key:
        return _problem(428, "mutation_precondition_required")
    try:
        request.app.state.upload_sessions.authorize(
            session_id, x_tara_job_secret, mutable=True
        )
        result = _idempotent(
            request,
            "cancel_upload_session",
            session_id,
            idempotency_key,
            {"session_id": session_id, "expected_revision": expected_revision},
            lambda: {
                "revision": request.app.state.audio_upload_repository.cancel_session(
                    session_id, expected_revision
                )
            },
        )
        for row in request.app.state.audio_upload_repository.files_for_session(
            session_id,
            limit=request.app.state.runtime_config.web.limits.max_upload_files,
        ):
            _cleanup_file(request, session_id, str(row["public_id"]))
        return result
    except UploadUnauthorized:
        return _problem(404, "upload_resource_unavailable")
    except (DatabaseConflict, IdempotencyConflict):
        return _problem(409, "resource_revision_conflict")
