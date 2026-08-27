"""Canonical public projections derived from current server state."""

from __future__ import annotations

from tara.web_contracts import ErrorCode, StageCode
from tara_web.api.schemas import JobSnapshot, SessionSnapshot
from tara_web.domain.enums import JobStatus, UploadFileStatus, UploadSessionStatus
from tara_web.domain.state_machines import (
    allowed_actions_for_file,
    allowed_actions_for_job,
    allowed_actions_for_session,
)
from tara_web.services.audio_formats import AUDIO_EXTENSIONS, AUDIO_FORMAT_LABEL

_VALIDATION_ERROR_MESSAGES = {
    "input_invalid": "upload.audio_invalid",
    "validation_unavailable": "upload.validation_unavailable",
    "zip_archive_too_large": "upload.zip_archive_too_large",
    "zip_compression_ratio_too_high": "upload.zip_compression_ratio_too_high",
    "zip_corrupted": "upload.zip_corrupted",
    "zip_duplicate_path": "upload.zip_duplicate_path",
    "zip_empty": "upload.zip_empty",
    "zip_encrypted": "upload.zip_encrypted",
    "zip_integrity_failed": "upload.zip_integrity_failed",
    "zip_invalid": "upload.zip_invalid",
    "zip_invalid_metadata": "upload.zip_invalid",
    "zip_no_supported_audio": "upload.zip_no_supported_audio",
    "zip_non_regular_entry": "upload.zip_non_regular_entry",
    "zip_timeout": "upload.zip_timeout",
    "zip_too_many_entries": "upload.zip_too_many_entries",
    "zip_uncompressed_too_large": "upload.zip_uncompressed_too_large",
    "zip_unsafe_path": "upload.zip_unsafe_path",
}

_INPUT_TOO_LARGE_CODES = {
    "input_too_large",
    "zip_archive_too_large",
    "zip_compression_ratio_too_high",
    "zip_uncompressed_too_large",
}


def _file_error(item: object) -> dict[str, object] | None:
    code = item["validation_error_code"]
    if not code:
        return None
    filename = str(item["display_name"] or "")
    if code == "input_type_mismatch":
        provided = filename.rsplit(".", 1)[-1].upper() if "." in filename else "?"
        detected = str(item["detected_type"] or "?").upper()
        return {
            "code": ErrorCode.INPUT_INVALID,
            "message_key": "upload.audio_format_mismatch",
            "parameters": {
                "expected": AUDIO_FORMAT_LABEL,
                "provided": provided,
                "detected": detected,
            },
        }
    if code in _INPUT_TOO_LARGE_CODES:
        public_code = ErrorCode.INPUT_TOO_LARGE
    elif code == "zip_timeout":
        public_code = ErrorCode.TIMEOUT
    else:
        try:
            public_code = ErrorCode(code)
        except ValueError:
            public_code = ErrorCode.INPUT_INVALID
    return {
        "code": public_code,
        "message_key": (
            "upload.audio_duration_too_long"
            if code == "input_too_large"
            and filename.rsplit(".", 1)[-1].lower() in AUDIO_EXTENSIONS
            else _VALIDATION_ERROR_MESSAGES.get(
                str(code),
                (
                    "errors.input_too_large"
                    if code == "input_too_large"
                    else "upload.validation_failed"
                ),
            )
        ),
        "parameters": (
            {"path": item["validation_error_path"]}
            if item["validation_error_path"]
            else {}
        ),
    }


def session_snapshot(request: object, session_id: str) -> dict[str, object] | None:
    repository = request.app.state.audio_upload_repository
    row = repository.session(session_id)
    if row is None:
        return None
    files = repository.files_for_session(
        session_id, limit=request.app.state.runtime_config.web.limits.max_upload_files
    )
    return SessionSnapshot.model_validate(
        {
            "session_id": session_id,
            "revision": row["revision"],
            "status": row["status"],
            "input_type": "zip" if row["archive_mode"] else row["input_type"],
            "archive_excluded_count": row["archive_excluded_count"],
            "archive_phase": row["archive_phase"],
            "expires_at": row["expires_at"],
            "language": row["language"],
            "context_text": row["context_text"],
            "previous_summaries_text": row["previous_summaries_text"],
            "files": [
                {
                    "file_id": item["public_id"],
                    "revision": item["revision"],
                    "status": item["status"],
                    "display_name": item["display_name"],
                    "archive_entry_name": item["archive_entry_name"],
                    "person": item["person"],
                    "confirmed_offset": item["confirmed_offset"],
                    "total_size": item["declared_bytes"],
                    "warning": (
                        {"code": item["validation_warning_code"], "parameters": {}}
                        if item["validation_warning_code"]
                        else None
                    ),
                    "error": _file_error(item),
                    "schema_name": item["schema_name"],
                    "schema_version": item["schema_version"],
                    "token_count": item["token_count"],
                    "allowed_actions": sorted(
                        action.value
                        for action in allowed_actions_for_file(
                            UploadFileStatus(item["status"])
                        )
                    ),
                }
                for item in files
            ],
            "validations": [],
            "allowed_actions": sorted(
                action.value
                for action in allowed_actions_for_session(
                    UploadSessionStatus(row["status"])
                )
            ),
        }
    ).model_dump(mode="json")


def job_snapshot(request: object, row: dict[str, object]) -> dict[str, object]:
    stage = row.get("stage") or StageCode.QUEUED.value
    stages = []
    seen_current = False
    for code in StageCode:
        if code.value == stage:
            status = (
                "active"
                if row["status"]
                in {"queued", "running", "cancel_requested", "stopping"}
                else ("completed" if row["status"] == "completed" else "failed")
            )
            seen_current = True
        elif not seen_current:
            status = "completed"
        else:
            status = "pending"
        stages.append(
            {
                "code": code.value,
                "status": status,
                "progress": (
                    int(row.get("stage_progress_milli", 0)) / 1000
                    if code.value == stage
                    else None
                ),
            }
        )
    error = None
    if row.get("error_code"):
        error_code = str(row["error_code"])
        # Older workers classified every unexpected pipeline failure as a
        # generic processing error. The persisted stage still identifies a
        # transcription failure without exposing provider details.
        if (
            error_code == ErrorCode.PROCESSING_FAILED.value
            and stage == StageCode.TRANSCRIPTION.value
        ):
            error_code = ErrorCode.TRANSCRIPTION_FAILED.value
        error = {
            "code": error_code,
            "message_key": f"errors.{error_code}",
            "parameters": {},
        }
    status = JobStatus(row["status"])
    estimated_remaining_ms = row.get("estimated_remaining_ms")
    if (
        estimated_remaining_ms is None
        and request.app.state.runtime_config.web.runner_mode == "tara"
        and status in {JobStatus.QUEUED, JobStatus.RUNNING}
    ):
        estimated_remaining_ms = (
            request.app.state.estimation_service.estimate_remaining_ms(
                str(row["public_id"])
            )
        )
    connection = request.app.state.database.connect()
    try:
        inputs = connection.execute(
            "SELECT public_id,status,display_name,confirmed_offset,declared_bytes,"
            "validation_warning_code,schema_name,schema_version,token_count "
            "FROM upload_files WHERE job_id=? AND active=1 "
            "ORDER BY id",
            (row["id"],),
        ).fetchall()
        identical_relaunch_available = False
    finally:
        connection.close()
    return JobSnapshot.model_validate(
        {
            "job_id": row["public_id"],
            "revision": row["revision"],
            "status": status.value,
            "attempt_number": row["current_attempt_number"],
            "language": row["language"],
            "started_at": row["started_at"],
            "expires_at": row["expires_at"],
            "progress": {
                "stage": stage,
                "substage_code": row.get("substage"),
                "current_ratio": int(row.get("stage_progress_milli", 0)) / 1000,
                "overall_ratio": int(row.get("total_progress_milli", 0)) / 1000,
                "estimate_seconds": (
                    int(estimated_remaining_ms) // 1000
                    if estimated_remaining_ms is not None
                    else None
                ),
                "estimate_status": (
                    "available"
                    if estimated_remaining_ms is not None
                    else "unavailable"
                ),
            },
            "stages": stages,
            "inputs": [
                {
                    "file_id": item["public_id"],
                    "status": item["status"],
                    "display_name": item["display_name"],
                    "confirmed_offset": item["confirmed_offset"],
                    "total_size": item["declared_bytes"],
                    "warning": (
                        {"code": item["validation_warning_code"], "parameters": {}}
                        if item["validation_warning_code"]
                        else None
                    ),
                    "error": None,
                    "schema_name": item["schema_name"],
                    "schema_version": item["schema_version"],
                    "token_count": item["token_count"],
                    "allowed_actions": sorted(
                        action.value
                        for action in allowed_actions_for_file(
                            UploadFileStatus(item["status"])
                        )
                    ),
                }
                for item in inputs
            ],
            "warnings": [],
            "error": error,
            "identical_relaunch_available": identical_relaunch_available,
            "allowed_actions": sorted(
                action.value
                for action in allowed_actions_for_job(
                    status,
                    identical_relaunch_available=identical_relaunch_available,
                )
            ),
        }
    ).model_dump(mode="json")
