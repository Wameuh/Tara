# ruff: noqa: E501
"""Explicit deny-by-default transition tables for mutable web resources."""

from collections.abc import Mapping
from enum import StrEnum

from tara_web.domain.enums import (
    AllowedAction,
    ArtifactStorageState,
    JobStatus,
    UploadFileStatus,
    UploadSessionStatus,
    ValidationStatus,
)

SESSION_TRANSITIONS: Mapping[UploadSessionStatus, Mapping[str, UploadSessionStatus]] = {
    UploadSessionStatus.CREATED: {
        "start_upload": UploadSessionStatus.UPLOADING,
        "cancel": UploadSessionStatus.CANCELLED,
        "expire": UploadSessionStatus.EXPIRED,
    },
    UploadSessionStatus.UPLOADING: {
        "start_validation": UploadSessionStatus.VALIDATING,
        "cancel": UploadSessionStatus.CANCELLED,
        "expire": UploadSessionStatus.EXPIRED,
    },
    UploadSessionStatus.VALIDATING: {
        "validation_ready": UploadSessionStatus.READY,
        "capacity_unavailable": UploadSessionStatus.WAITING_FOR_CAPACITY,
        "cancel": UploadSessionStatus.CANCELLED,
        "expire": UploadSessionStatus.EXPIRED,
    },
    UploadSessionStatus.READY: {
        "claim": UploadSessionStatus.CONSUMED,
        "capacity_unavailable": UploadSessionStatus.WAITING_FOR_CAPACITY,
        "cancel": UploadSessionStatus.CANCELLED,
        "expire": UploadSessionStatus.EXPIRED,
    },
    UploadSessionStatus.WAITING_FOR_CAPACITY: {
        "capacity_available": UploadSessionStatus.READY,
        "cancel": UploadSessionStatus.CANCELLED,
        "expire": UploadSessionStatus.EXPIRED,
    },
    UploadSessionStatus.CONSUMED: {"expire": UploadSessionStatus.EXPIRED},
    UploadSessionStatus.CANCELLED: {"expire": UploadSessionStatus.EXPIRED},
    UploadSessionStatus.EXPIRED: {},
}
FILE_TRANSITIONS: Mapping[UploadFileStatus, Mapping[str, UploadFileStatus]] = {
    UploadFileStatus.CREATED: {
        "start_upload": UploadFileStatus.UPLOADING,
        "delete": UploadFileStatus.DELETED,
    },
    UploadFileStatus.UPLOADING: {
        "finalize": UploadFileStatus.FINALIZING,
        "delete": UploadFileStatus.DELETED,
    },
    UploadFileStatus.FINALIZING: {
        "verify": UploadFileStatus.VERIFYING,
        "invalidate": UploadFileStatus.INVALID,
        "delete": UploadFileStatus.DELETED,
    },
    UploadFileStatus.VERIFYING: {
        "accept": UploadFileStatus.READY,
        "invalidate": UploadFileStatus.INVALID,
        "delete": UploadFileStatus.DELETED,
    },
    UploadFileStatus.READY: {
        "replace": UploadFileStatus.REPLACED,
        "delete": UploadFileStatus.DELETED,
    },
    UploadFileStatus.INVALID: {
        "retry_finalization": UploadFileStatus.FINALIZING,
        "replace": UploadFileStatus.REPLACED,
        "delete": UploadFileStatus.DELETED,
    },
    UploadFileStatus.REPLACED: {},
    UploadFileStatus.DELETED: {},
}
VALIDATION_TRANSITIONS: Mapping[ValidationStatus, Mapping[str, ValidationStatus]] = {
    ValidationStatus.QUEUED: {
        "start": ValidationStatus.RUNNING,
        "cancel": ValidationStatus.CANCELLED,
    },
    ValidationStatus.RUNNING: {
        "request_cancel": ValidationStatus.CANCEL_REQUESTED,
        "complete": ValidationStatus.COMPLETED,
        "fail": ValidationStatus.FAILED,
        "server_restart": ValidationStatus.QUEUED,
    },
    ValidationStatus.CANCEL_REQUESTED: {
        "acknowledge_cancel": ValidationStatus.STOPPING,
        "server_restart": ValidationStatus.CANCELLED,
    },
    ValidationStatus.STOPPING: {
        "cancelled": ValidationStatus.CANCELLED,
        "cancel_timeout": ValidationStatus.CANCEL_FAILED,
        "server_restart": ValidationStatus.CANCELLED,
    },
    ValidationStatus.COMPLETED: {},
    ValidationStatus.FAILED: {},
    ValidationStatus.CANCELLED: {},
    ValidationStatus.CANCEL_FAILED: {},
}
JOB_TRANSITIONS: Mapping[JobStatus, Mapping[str, JobStatus]] = {
    JobStatus.QUEUED: {
        "start": JobStatus.RUNNING,
        "cancel": JobStatus.CANCELLED,
    },
    JobStatus.RUNNING: {
        "request_cancel": JobStatus.CANCEL_REQUESTED,
        "complete": JobStatus.COMPLETED,
        "fail": JobStatus.FAILED,
        "timeout": JobStatus.TIMED_OUT,
        "server_restart": JobStatus.FAILED,
    },
    JobStatus.CANCEL_REQUESTED: {
        "acknowledge_cancel": JobStatus.STOPPING,
        "complete": JobStatus.COMPLETED,
        "server_restart": JobStatus.FAILED,
    },
    JobStatus.STOPPING: {
        "cancelled": JobStatus.CANCELLED,
        "cancel_timeout": JobStatus.CANCEL_FAILED,
        "server_restart": JobStatus.FAILED,
    },
    JobStatus.COMPLETED: {"expire": JobStatus.EXPIRED, "delete": JobStatus.DELETED},
    JobStatus.FAILED: {"expire": JobStatus.EXPIRED, "delete": JobStatus.DELETED},
    JobStatus.TIMED_OUT: {
        "expire": JobStatus.EXPIRED,
        "delete": JobStatus.DELETED,
    },
    JobStatus.CANCELLED: {"expire": JobStatus.EXPIRED, "delete": JobStatus.DELETED},
    JobStatus.CANCEL_FAILED: {"expire": JobStatus.EXPIRED, "delete": JobStatus.DELETED},
    JobStatus.EXPIRED: {"delete": JobStatus.DELETED},
    JobStatus.DELETED: {},
}
ARTIFACT_TRANSITIONS: Mapping[
    ArtifactStorageState, Mapping[str, ArtifactStorageState]
] = {
    ArtifactStorageState.PENDING: {
        "write_ready": ArtifactStorageState.READY,
        "write_error": ArtifactStorageState.ERROR,
    },
    ArtifactStorageState.READY: {"delete": ArtifactStorageState.DELETING},
    ArtifactStorageState.DELETING: {
        "deleted": ArtifactStorageState.DELETED,
        "delete_error": ArtifactStorageState.ERROR,
    },
    ArtifactStorageState.ERROR: {"delete": ArtifactStorageState.DELETING},
    ArtifactStorageState.DELETED: {},
}


def transition[E: StrEnum](
    table: Mapping[E, Mapping[str, E]], current: E, command: str
) -> E:
    """Return the only legal next state, otherwise reject the command."""
    try:
        return table[current][command]
    except KeyError as exc:
        raise ValueError(
            f"illegal transition: {current.value} --{command}--> ?"
        ) from exc


def assert_monotonic_revision(previous: int, candidate: int) -> None:
    if candidate <= previous:
        raise ValueError("resource revision must increase monotonically")


def allowed_actions_for_session(
    status: UploadSessionStatus,
) -> frozenset[AllowedAction]:
    actions: set[AllowedAction] = set()
    if status not in {UploadSessionStatus.EXPIRED, UploadSessionStatus.CONSUMED}:
        actions.add(AllowedAction.REGENERATE_SECRET)
    if status in {
        UploadSessionStatus.CREATED,
        UploadSessionStatus.UPLOADING,
        UploadSessionStatus.VALIDATING,
        UploadSessionStatus.READY,
        UploadSessionStatus.WAITING_FOR_CAPACITY,
    }:
        actions.add(AllowedAction.CANCEL)
    if status == UploadSessionStatus.READY:
        actions.add(AllowedAction.LAUNCH)
    return frozenset(actions)


def allowed_actions_for_file(status: UploadFileStatus) -> frozenset[AllowedAction]:
    if status == UploadFileStatus.INVALID:
        return frozenset(
            {
                AllowedAction.RETRY_FINALIZATION,
                AllowedAction.REPLACE_FILE,
                AllowedAction.DELETE_FILE,
            }
        )
    if status == UploadFileStatus.READY:
        return frozenset({AllowedAction.REPLACE_FILE, AllowedAction.DELETE_FILE})
    return frozenset()


def allowed_actions_for_validation(
    status: ValidationStatus,
) -> frozenset[AllowedAction]:
    if status in {ValidationStatus.QUEUED, ValidationStatus.RUNNING}:
        return frozenset({AllowedAction.CANCEL})
    return frozenset()


def allowed_actions_for_job(
    status: JobStatus,
    *,
    identical_relaunch_available: bool = False,
) -> frozenset[AllowedAction]:
    actions: set[AllowedAction] = set()
    if status not in {JobStatus.EXPIRED, JobStatus.DELETED}:
        actions.add(AllowedAction.REGENERATE_SECRET)
    if status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        actions.add(AllowedAction.CANCEL)
    if status == JobStatus.COMPLETED:
        actions.add(AllowedAction.VIEW_RESULT)
    terminal_statuses = {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
        JobStatus.CANCELLED,
        JobStatus.CANCEL_FAILED,
    }
    if status in terminal_statuses:
        actions.add(AllowedAction.DELETE_JOB)
    if status in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.CANCEL_FAILED}:
        actions.add(AllowedAction.EDIT_AND_RELAUNCH)
    if status == JobStatus.TIMED_OUT:
        actions.add(AllowedAction.EDIT_AND_RELAUNCH)
    return frozenset(actions)
