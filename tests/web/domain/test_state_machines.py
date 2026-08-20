# ruff: noqa: E501
from __future__ import annotations

import pytest

from tara_web.domain.enums import (
    JobStatus,
    UploadFileStatus,
    UploadSessionStatus,
    ValidationStatus,
)
from tara_web.domain.state_machines import (
    ARTIFACT_TRANSITIONS,
    FILE_TRANSITIONS,
    JOB_TRANSITIONS,
    SESSION_TRANSITIONS,
    VALIDATION_TRANSITIONS,
    allowed_actions_for_file,
    allowed_actions_for_job,
    allowed_actions_for_session,
    allowed_actions_for_validation,
    assert_monotonic_revision,
    transition,
)


@pytest.mark.parametrize(
    "table",
    [
        SESSION_TRANSITIONS,
        FILE_TRANSITIONS,
        VALIDATION_TRANSITIONS,
        JOB_TRANSITIONS,
        ARTIFACT_TRANSITIONS,
    ],
)
def test_every_declared_transition_is_accepted(table: object) -> None:
    for current, commands in table.items():  # type: ignore[union-attr]
        for command, expected in commands.items():
            assert transition(table, current, command) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "table",
    [
        SESSION_TRANSITIONS,
        FILE_TRANSITIONS,
        VALIDATION_TRANSITIONS,
        JOB_TRANSITIONS,
        ARTIFACT_TRANSITIONS,
    ],
)
def test_every_undeclared_state_command_pair_is_rejected(table: object) -> None:
    commands = {"invented"}
    for declared_commands in table.values():  # type: ignore[union-attr]
        commands.update(declared_commands)
    for state, declared_commands in table.items():  # type: ignore[union-attr]
        for command in commands - set(declared_commands):
            with pytest.raises(ValueError, match="illegal transition"):
                transition(table, state, command)  # type: ignore[arg-type]


def test_revision_must_strictly_increase() -> None:
    assert_monotonic_revision(4, 5)
    with pytest.raises(ValueError):
        assert_monotonic_revision(4, 4)
    with pytest.raises(ValueError):
        assert_monotonic_revision(4, 3)


def test_restart_and_cancellation_invariants_are_explicit() -> None:
    assert (
        transition(JOB_TRANSITIONS, JobStatus.RUNNING, "server_restart")
        == JobStatus.FAILED
    )
    with pytest.raises(ValueError, match="illegal transition"):
        transition(JOB_TRANSITIONS, JobStatus.QUEUED, "server_restart")
    with pytest.raises(ValueError, match="illegal transition"):
        transition(JOB_TRANSITIONS, JobStatus.RUNNING, "expire")
    assert (
        transition(JOB_TRANSITIONS, JobStatus.QUEUED, "cancel") == JobStatus.CANCELLED
    )
    assert (
        transition(JOB_TRANSITIONS, JobStatus.CANCEL_REQUESTED, "acknowledge_cancel")
        == JobStatus.STOPPING
    )
    assert (
        transition(JOB_TRANSITIONS, JobStatus.STOPPING, "cancel_timeout")
        == JobStatus.CANCEL_FAILED
    )
    assert (
        transition(VALIDATION_TRANSITIONS, ValidationStatus.RUNNING, "server_restart")
        == ValidationStatus.QUEUED
    )
    assert (
        transition(
            VALIDATION_TRANSITIONS,
            ValidationStatus.CANCEL_REQUESTED,
            "server_restart",
        )
        == ValidationStatus.CANCELLED
    )
    assert (
        transition(VALIDATION_TRANSITIONS, ValidationStatus.STOPPING, "server_restart")
        == ValidationStatus.CANCELLED
    )


def test_allowed_actions_are_pure_functions_of_resource_state() -> None:
    assert allowed_actions_for_job(JobStatus.RUNNING) == {
        "cancel",
        "regenerate_secret",
    }
    assert allowed_actions_for_job(JobStatus.COMPLETED) == {
        "delete_job",
        "regenerate_secret",
        "view_result",
    }
    assert allowed_actions_for_session(UploadSessionStatus.READY) == {
        "cancel",
        "launch",
        "regenerate_secret",
    }
    assert allowed_actions_for_file(UploadFileStatus.INVALID) == {
        "delete_file",
        "replace_file",
        "retry_finalization",
    }
    assert (
        transition(FILE_TRANSITIONS, UploadFileStatus.INVALID, "retry_finalization")
        == UploadFileStatus.FINALIZING
    )
    assert (
        transition(VALIDATION_TRANSITIONS, ValidationStatus.RUNNING, "request_cancel")
        == ValidationStatus.CANCEL_REQUESTED
    )
    assert allowed_actions_for_validation(ValidationStatus.QUEUED) == {"cancel"}
    assert allowed_actions_for_validation(ValidationStatus.CANCEL_REQUESTED) == set()
    assert allowed_actions_for_job(JobStatus.TIMED_OUT) == {
        "delete_job",
        "edit_and_relaunch",
        "regenerate_secret",
    }
    assert allowed_actions_for_job(
        JobStatus.TIMED_OUT,
        identical_relaunch_available=True,
    ) == {
        "delete_job",
        "edit_and_relaunch",
        "regenerate_secret",
        "relaunch_identical",
    }
