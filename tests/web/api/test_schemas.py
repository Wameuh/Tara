# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tara_web.api.schemas import (
    JobSnapshot,
    ProblemDetails,
    PublicError,
    PublicEventEnvelope,
    SessionSnapshot,
    UploadFileSnapshot,
    ValidationSnapshot,
)


def job_payload() -> dict[str, object]:
    return {
        "job_id": "job_1",
        "revision": 2,
        "status": "completed",
        "attempt_number": 1,
        "language": "fr",
        "started_at": datetime.now(UTC),
        "allowed_actions": ["delete_job", "regenerate_secret", "view_result"],
    }


def test_public_snapshot_is_strict_and_has_no_secret_field() -> None:
    assert JobSnapshot.model_validate(job_payload()).job_id == "job_1"
    with pytest.raises(ValidationError, match="secret"):
        JobSnapshot.model_validate(job_payload() | {"secret": "never-public"})
    with pytest.raises(ValidationError, match="server_path"):
        JobSnapshot.model_validate(job_payload() | {"server_path": "/srv/jobs/job_1"})


def test_snapshot_requires_consistent_status_and_actions() -> None:
    payload = job_payload() | {"status": "cancel_failed", "allowed_actions": []}
    with pytest.raises(ValidationError, match="public error"):
        JobSnapshot.model_validate(payload)


def test_file_actions_are_targeted_in_each_file_snapshot() -> None:
    invalid_file = UploadFileSnapshot(
        file_id="file_1",
        status="invalid",
        confirmed_offset=1,
        total_size=1,
        allowed_actions=["delete_file", "replace_file", "retry_finalization"],
    )
    ready_file = UploadFileSnapshot(
        file_id="file_2",
        status="ready",
        confirmed_offset=1,
        total_size=1,
        allowed_actions=["delete_file", "replace_file"],
    )
    snapshot = SessionSnapshot(
        session_id="session_1",
        revision=1,
        status="uploading",
        language="fr",
        files=(invalid_file, ready_file),
        next_files_cursor="cursor_after_file_2",
        validations=(
            ValidationSnapshot(
                validation_id="validation_1",
                target_file_id="file_1",
                status="running",
                progress=0.5,
                allowed_actions=["cancel"],
            ),
        ),
        next_validations_cursor="cursor_after_validation_1",
        expires_at=datetime.now(UTC),
        allowed_actions=["cancel", "regenerate_secret"],
    )
    assert snapshot.files[0].allowed_actions != snapshot.files[1].allowed_actions
    assert snapshot.validations[0].target_file_id == "file_1"
    payload = job_payload() | {"allowed_actions": ["delete_job", "view_result"]}
    with pytest.raises(ValidationError, match="derived"):
        JobSnapshot.model_validate(payload)


def test_session_files_are_paginated_and_validation_actions_are_derived() -> None:
    file = UploadFileSnapshot(
        file_id="file_1",
        status="ready",
        confirmed_offset=1,
        total_size=1,
        allowed_actions=["delete_file", "replace_file"],
    )
    session_payload = {
        "session_id": "session_1",
        "revision": 1,
        "status": "uploading",
        "files": [file] * 101,
        "expires_at": datetime.now(UTC),
        "allowed_actions": ["cancel", "regenerate_secret"],
    }
    with pytest.raises(ValidationError):
        SessionSnapshot.model_validate(session_payload)
    validations_payload = session_payload | {
        "files": [file],
        "validations": [
            {
                "validation_id": f"validation_{index}",
                "target_file_id": "file_1",
                "status": "completed",
                "progress": 1,
                "allowed_actions": [],
            }
            for index in range(101)
        ],
    }
    with pytest.raises(ValidationError):
        SessionSnapshot.model_validate(validations_payload)
    with pytest.raises(ValidationError, match="validation state"):
        ValidationSnapshot(
            validation_id="validation_1",
            target_file_id="file_1",
            status="completed",
            progress=1,
            allowed_actions=["cancel"],
        )
    with pytest.raises(ValidationError, match="failed validations require"):
        ValidationSnapshot(
            validation_id="validation_1",
            target_file_id="file_1",
            status="failed",
            progress=1,
        )
    with pytest.raises(ValidationError, match="failed validations require"):
        ValidationSnapshot(
            validation_id="validation_1",
            target_file_id="file_1",
            status="cancel_failed",
            progress=1,
        )
    with pytest.raises(ValidationError, match="cancel_failed validations require"):
        ValidationSnapshot(
            validation_id="validation_1",
            target_file_id="file_1",
            status="cancel_failed",
            progress=1,
            error={"code": "processing_failed", "message_key": "errors.processing"},
        )
    with pytest.raises(ValidationError, match="non-failed validations"):
        ValidationSnapshot(
            validation_id="validation_1",
            target_file_id="file_1",
            status="completed",
            progress=1,
            error={"code": "processing_failed", "message_key": "errors.processing"},
        )


def test_terminal_job_error_codes_and_public_numbers_are_finite() -> None:
    timed_out = job_payload() | {
        "status": "timed_out",
        "error": {"code": "processing_failed", "message_key": "errors.processing"},
        "allowed_actions": ["delete_job", "edit_and_relaunch", "regenerate_secret"],
    }
    with pytest.raises(ValidationError, match="timed_out jobs require timeout"):
        JobSnapshot.model_validate(timed_out)
    cancel_failed = job_payload() | {
        "status": "cancel_failed",
        "error": {"code": "timeout", "message_key": "errors.timeout"},
        "allowed_actions": ["delete_job", "edit_and_relaunch", "regenerate_secret"],
    }
    with pytest.raises(
        ValidationError, match="cancel_failed jobs require cancel_failed"
    ):
        JobSnapshot.model_validate(cancel_failed)
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError, match="finite"):
            PublicError(
                code="input_too_large",
                message_key="errors.input_too_large",
                parameters={"limit": value},
            )
        with pytest.raises(ValidationError, match="finite"):
            PublicEventEnvelope(
                type="warning_raised", revision=1, data={"ratio": value}
            )


def test_event_payload_and_problem_details_are_bounded() -> None:
    with pytest.raises(ValidationError):
        PublicEventEnvelope(
            type="snapshot_updated", revision=1, data={"value": "x" * 20_000}
        )
    problem = ProblemDetails(
        type="https://tara.invalid/problems/input",
        title="Input rejected",
        status=422,
        code="input_invalid",
        correlation_id="corr_1",
    )
    assert problem.code == "input_invalid"
    with pytest.raises(ValidationError):
        PublicError(
            code="input_invalid",
            message_key="errors.input_invalid",
            parameters={"detail": "x" * 100_000},
        )
    with pytest.raises(ValidationError):
        ProblemDetails(
            type="https://tara.invalid/problems/input",
            title="Input rejected",
            status=422,
            code="input_invalid",
            correlation_id="corr_1",
            errors=tuple(
                PublicError(
                    code="input_invalid",
                    message_key="errors.input_invalid",
                )
                for index in range(33)
            ),
        )
