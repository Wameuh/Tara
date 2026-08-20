"""Stable V1 vocabulary owned by the web domain."""

from enum import StrEnum

from tara.web_contracts import ArtifactType, ErrorCode, StageCode, WarningCode

__all__ = [
    "AllowedAction",
    "ArtifactStorageState",
    "ArtifactType",
    "ErrorCode",
    "JobStatus",
    "RetentionKind",
    "StageCode",
    "UploadFileStatus",
    "UploadSessionStatus",
    "ValidationStatus",
    "WarningCode",
]


class UploadSessionStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    VALIDATING = "validating"
    READY = "ready"
    WAITING_FOR_CAPACITY = "waiting_for_capacity"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class UploadFileStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    FINALIZING = "finalizing"
    VERIFYING = "verifying"
    READY = "ready"
    INVALID = "invalid"
    REPLACED = "replaced"
    DELETED = "deleted"


class ValidationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCEL_FAILED = "cancel_failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CANCEL_FAILED = "cancel_failed"
    EXPIRED = "expired"
    DELETED = "deleted"


class ArtifactStorageState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DELETING = "deleting"
    DELETED = "deleted"
    ERROR = "error"


class AllowedAction(StrEnum):
    CANCEL = "cancel"
    RETRY_FINALIZATION = "retry_finalization"
    REPLACE_FILE = "replace_file"
    DELETE_FILE = "delete_file"
    LAUNCH = "launch"
    RELAUNCH_IDENTICAL = "relaunch_identical"
    EDIT_AND_RELAUNCH = "edit_and_relaunch"
    DELETE_JOB = "delete_job"
    REGENERATE_SECRET = "regenerate_secret"
    VIEW_RESULT = "view_result"


class RetentionKind(StrEnum):
    INTERMEDIATE = "intermediate"
    FINAL_RESULT = "final_result"
