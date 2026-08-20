# ruff: noqa: E501
"""Strict public API shapes. Internal data must never be added to these models."""

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tara.web_contracts import MAX_IPC_EVENT_BYTES
from tara_web.domain.enums import (
    AllowedAction,
    ErrorCode,
    JobStatus,
    StageCode,
    UploadFileStatus,
    UploadSessionStatus,
    ValidationStatus,
    WarningCode,
)
from tara_web.domain.state_machines import (
    allowed_actions_for_file,
    allowed_actions_for_job,
    allowed_actions_for_session,
    allowed_actions_for_validation,
)

PublicText = Annotated[str, Field(max_length=1_024)]
PublicParameter = Annotated[str, Field(max_length=1_024)] | int | float | bool
PublicParameters = dict[
    Annotated[str, Field(pattern=r"^[a-z0-9_-]+$", max_length=64)], PublicParameter
]


class ApiProblemCode(StrEnum):
    BAD_REQUEST = "bad_request"
    FORBIDDEN = "forbidden"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    PRECONDITION_REQUIRED = "precondition_required"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def reject_non_finite_numbers(self) -> "PublicModel":
        if not _public_numbers_are_finite(self.model_dump()):
            raise ValueError("public numeric values must be finite")
        return self


class PublicError(PublicModel):
    code: ErrorCode
    message_key: Annotated[str, Field(pattern=r"^[a-z0-9_.-]+$", max_length=128)]
    parameters: PublicParameters = Field(default_factory=dict, max_length=16)


class PublicWarning(PublicModel):
    code: WarningCode
    parameters: PublicParameters = Field(default_factory=dict, max_length=16)


class ProgressSnapshot(PublicModel):
    stage: StageCode
    substage_code: Annotated[
        str | None, Field(default=None, pattern=r"^[a-z0-9_-]+$", max_length=128)
    ]
    current_ratio: Annotated[float | None, Field(default=None, ge=0, le=1)]
    overall_ratio: Annotated[float | None, Field(default=None, ge=0, le=1)]
    estimate_seconds: Annotated[int | None, Field(default=None, ge=0)]
    estimate_status: Literal["available", "unavailable"] = "unavailable"


class StageSnapshot(PublicModel):
    code: StageCode
    status: Literal["pending", "active", "completed", "skipped", "failed"]
    progress: Annotated[float | None, Field(default=None, ge=0, le=1)]


class UploadFileSnapshot(PublicModel):
    file_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    revision: Annotated[int, Field(ge=1)] = 1
    status: UploadFileStatus
    display_name: Annotated[str | None, Field(default=None, max_length=255)]
    archive_entry_name: Annotated[str | None, Field(default=None, max_length=1024)]
    person: Annotated[str | None, Field(default=None, max_length=255)]
    confirmed_offset: Annotated[int, Field(ge=0)]
    total_size: Annotated[int, Field(ge=0)]
    warning: PublicWarning | None = None
    error: PublicError | None = None
    schema_name: Annotated[str | None, Field(default=None, max_length=128)]
    schema_version: Annotated[str | None, Field(default=None, max_length=64)]
    token_count: Annotated[int | None, Field(default=None, ge=0)]
    allowed_actions: frozenset[AllowedAction] = frozenset()

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> "UploadFileSnapshot":
        if self.allowed_actions != allowed_actions_for_file(self.status):
            raise ValueError("allowed_actions must be derived from the file state")
        return self


class ValidationSnapshot(PublicModel):
    validation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    target_file_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    status: ValidationStatus
    progress: Annotated[float | None, Field(default=None, ge=0, le=1)]
    error: PublicError | None = None
    allowed_actions: frozenset[AllowedAction] = frozenset()

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> "ValidationSnapshot":
        failed_statuses = {ValidationStatus.FAILED, ValidationStatus.CANCEL_FAILED}
        if self.status in failed_statuses and self.error is None:
            raise ValueError("failed validations require a public error")
        if self.status == ValidationStatus.CANCEL_FAILED and (
            self.error is None or self.error.code != ErrorCode.CANCEL_FAILED
        ):
            raise ValueError("cancel_failed validations require cancel_failed")
        if self.status not in failed_statuses and self.error is not None:
            raise ValueError("non-failed validations cannot include a public error")
        if self.allowed_actions != allowed_actions_for_validation(self.status):
            raise ValueError(
                "allowed_actions must be derived from the validation state"
            )
        return self


class SessionSnapshot(PublicModel):
    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    revision: Annotated[int, Field(ge=0)]
    status: UploadSessionStatus
    input_type: Literal["audio", "merged_transcription", "zip"] = "audio"
    archive_excluded_count: Annotated[int, Field(ge=0)] = 0
    archive_phase: Literal[
        "transfer", "extraction", "track_validation", "launch_preparation"
    ] | None = None
    files: Annotated[tuple[UploadFileSnapshot, ...], Field(max_length=100)] = ()
    next_files_cursor: Annotated[str | None, Field(default=None, max_length=512)]
    validations: Annotated[tuple[ValidationSnapshot, ...], Field(max_length=100)] = ()
    next_validations_cursor: Annotated[str | None, Field(default=None, max_length=512)]
    expires_at: datetime
    allowed_actions: frozenset[AllowedAction] = frozenset()
    error: PublicError | None = None
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")]
    context_text: Annotated[str, Field(max_length=200_000)] = ""
    previous_summaries_text: Annotated[str, Field(max_length=2_000_000)] = ""

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> "SessionSnapshot":
        if self.allowed_actions != allowed_actions_for_session(self.status):
            raise ValueError("allowed_actions must be derived from the session state")
        return self


class JobSnapshot(PublicModel):
    job_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    revision: Annotated[int, Field(ge=0)]
    status: JobStatus
    attempt_number: Annotated[int, Field(ge=1)]
    language: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=32)]
    started_at: datetime | None = None
    expires_at: datetime | None = None
    progress: ProgressSnapshot | None = None
    stages: Annotated[tuple[StageSnapshot, ...], Field(max_length=32)] = ()
    inputs: Annotated[tuple[UploadFileSnapshot, ...], Field(max_length=100)] = ()
    warnings: Annotated[tuple[PublicWarning, ...], Field(max_length=32)] = ()
    error: PublicError | None = None
    allowed_actions: frozenset[AllowedAction] = frozenset()
    identical_relaunch_available: bool = False

    @model_validator(mode="after")
    def validate_status_shape(self) -> "JobSnapshot":
        if (
            self.status
            in {
                JobStatus.FAILED,
                JobStatus.TIMED_OUT,
                JobStatus.CANCEL_FAILED,
            }
            and self.error is None
        ):
            raise ValueError("failed jobs require a public error")
        expected_error_codes = {
            JobStatus.TIMED_OUT: ErrorCode.TIMEOUT,
            JobStatus.CANCEL_FAILED: ErrorCode.CANCEL_FAILED,
        }
        if self.status in expected_error_codes and self.error is not None:
            if self.error.code != expected_error_codes[self.status]:
                raise ValueError(
                    f"{self.status.value} jobs require {expected_error_codes[self.status].value}"
                )
        if self.allowed_actions != allowed_actions_for_job(
            self.status,
            identical_relaunch_available=self.identical_relaunch_available,
        ):
            raise ValueError("allowed_actions must be derived from the job state")
        return self


class PublicEventEnvelope(PublicModel):
    type: Literal["snapshot_updated", "warning_raised", "run_completed", "run_failed"]
    revision: Annotated[int, Field(ge=0)]
    data: dict[str, PublicParameter | None] = Field(default_factory=dict, max_length=16)

    @field_validator("data")
    @classmethod
    def bound_encoded_payload(cls, value: dict[str, object]) -> dict[str, object]:
        import json

        if (
            len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            > MAX_IPC_EVENT_BYTES
        ):
            raise ValueError("event data exceeds the public payload limit")
        return value


class ProblemDetails(PublicModel):
    type: Annotated[str, Field(max_length=256)]
    title: Annotated[str, Field(max_length=256)]
    status: Annotated[int, Field(ge=400, le=599)]
    code: ApiProblemCode | ErrorCode
    correlation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    errors: Annotated[tuple[PublicError, ...], Field(max_length=32)] = ()


class CommandAccepted(PublicModel):
    accepted: bool = True


class DeletedResponse(PublicModel):
    deleted: bool = True


class CreatedJob(PublicModel):
    job_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    revision: Annotated[int, Field(ge=1)]


class SessionInputsUpdate(PublicModel):
    """Owner-protected text inputs that are carried into job creation."""

    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")]
    context_text: Annotated[str, Field(max_length=200_000)] = ""
    previous_summaries_text: Annotated[str, Field(max_length=2_000_000)] = ""


class RelaunchSession(PublicModel):
    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]
    revision: Annotated[int, Field(ge=1)]


class RotatedSecret(PublicModel):
    secret: Annotated[str, Field(min_length=43, max_length=512)]
    revision: Annotated[int, Field(ge=1)]


class ResultParagraphBlock(PublicModel):
    type: Literal["paragraph"]
    text: Annotated[str, Field(max_length=20_000)]


class ResultListBlock(PublicModel):
    type: Literal["list", "orderedList"]
    items: Annotated[
        tuple[Annotated[str, Field(max_length=20_000)], ...], Field(max_length=200)
    ]


class ResultKeyValueEntry(PublicModel):
    key: Annotated[str, Field(max_length=256)]
    value: Annotated[str, Field(max_length=20_000)]


class ResultKeyValueBlock(PublicModel):
    type: Literal["keyValue"]
    entries: Annotated[tuple[ResultKeyValueEntry, ...], Field(max_length=200)]


class ResultTableBlock(PublicModel):
    type: Literal["table"]
    headers: Annotated[
        tuple[Annotated[str, Field(max_length=256)], ...], Field(max_length=32)
    ]
    rows: Annotated[
        tuple[tuple[Annotated[str, Field(max_length=10_000)], ...], ...],
        Field(max_length=500),
    ]


class ResultCalloutBlock(PublicModel):
    type: Literal["callout"]
    title: Annotated[str, Field(max_length=255)]
    text: Annotated[str, Field(max_length=20_000)]


ResultBlock = (
    ResultParagraphBlock
    | ResultListBlock
    | ResultKeyValueBlock
    | ResultTableBlock
    | ResultCalloutBlock
)


class ResultSection(PublicModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]+$", max_length=128)]
    order: Annotated[int, Field(ge=0)]
    title: Annotated[str, Field(max_length=255)]
    status: Literal["available", "partial"]
    text: Annotated[str, Field(max_length=100_000)]
    section_type: Literal[
        "overview",
        "chronology",
        "characters",
        "quests",
        "combat",
        "locations",
        "items",
        "factions",
        "uncertainties",
        "generic",
    ] = "generic"
    blocks: Annotated[tuple[ResultBlock, ...], Field(max_length=500)] = ()


class ResultCost(PublicModel):
    status: Literal["complete", "partial", "unavailable"]
    value_micro_eur: Annotated[int | None, Field(ge=0)]
    explanation_key: Literal[
        "result.cost_explanation",
        "result.cost_partial_explanation",
        "result.cost_unavailable_explanation",
    ]


class ResultSnapshot(PublicModel):
    type: Literal["tara_result_v1"]
    status: Literal["available", "expired"]
    expires_at: datetime | None = None
    sections: Annotated[tuple[ResultSection, ...], Field(max_length=100)] = ()
    cost: ResultCost


def _public_numbers_are_finite(value: object) -> bool:
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, dict):
        return all(_public_numbers_are_finite(item) for item in value.values())
    if isinstance(value, tuple | list):
        return all(_public_numbers_are_finite(item) for item in value)
    return True
