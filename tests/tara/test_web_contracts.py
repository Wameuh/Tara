# ruff: noqa: E501
from __future__ import annotations

import json

import pytest

from tara.web_contracts import (
    CONTRACT_VERSION,
    CancellationToken,
    ContractViolation,
    ErrorCode,
    EventSink,
    EventType,
    RunnerEvent,
    RunnerMetrics,
    RunnerRequest,
    RunnerResult,
    RunnerStatus,
    StageCode,
    TaraWebRunner,
    WarningCode,
    validate_ipc_event,
)


class Sink(EventSink):
    def __init__(self) -> None:
        self.events: list[RunnerEvent] = []

    def emit(self, event: RunnerEvent) -> None:
        self.events.append(event)


class Token(CancellationToken):
    def is_cancelled(self) -> bool:
        return False


class MinimalRunner(TaraWebRunner):
    def run(
        self,
        request: RunnerRequest,
        event_sink: EventSink,
        cancellation_token: CancellationToken,
    ) -> RunnerResult:
        event_sink.emit(RunnerEvent(CONTRACT_VERSION, EventType.RUN_COMPLETED, 1))
        return RunnerResult(CONTRACT_VERSION, RunnerStatus.COMPLETED)


class FakeRunner(MinimalRunner):
    pass


def request() -> RunnerRequest:
    return RunnerRequest(
        CONTRACT_VERSION,
        "job_1",
        1,
        "audio",
        "inputs/sources-manifest.json",
    )


@pytest.mark.parametrize("runner_type", [MinimalRunner, FakeRunner])
def test_runner_variants_satisfy_the_same_contract(
    runner_type: type[MinimalRunner],
) -> None:
    sink = Sink()
    result = runner_type().run(request(), sink, Token())
    assert result.status == "completed"
    assert sink.events[0].event_type == EventType.RUN_COMPLETED


def test_request_is_immutable_and_rejects_absolute_manifest_paths() -> None:
    value = request()
    with pytest.raises(AttributeError):
        value.job_id = "other"  # type: ignore[misc]
    with pytest.raises(ContractViolation, match="managed storage"):
        RunnerRequest(CONTRACT_VERSION, "job_1", 1, "audio", "C:/outside.json")
    with pytest.raises(ContractViolation, match="managed storage"):
        RunnerRequest(CONTRACT_VERSION, "job_1", 1, "audio", "C:outside.json")


def test_request_references_large_inputs_by_managed_path() -> None:
    request_with_text_files = RunnerRequest(
        CONTRACT_VERSION,
        "job_1",
        1,
        "audio",
        "inputs/sources-manifest.json",
        context_path="inputs/context.txt",
        previous_summaries_path="inputs/previous.yaml",
    )
    assert request_with_text_files.source_manifest_path == "inputs/sources-manifest.json"
    assert not hasattr(request_with_text_files, "context")
    assert not hasattr(request_with_text_files, "sources")


def test_request_uses_one_managed_manifest_instead_of_a_source_list() -> None:
    with pytest.raises(ContractViolation, match="source manifest"):
        RunnerRequest(CONTRACT_VERSION, "job_1", 1, "audio")
    with pytest.raises(ContractViolation, match="forbid a merged transcription path"):
        RunnerRequest(
            CONTRACT_VERSION,
            "job_1",
            1,
            "audio",
            "inputs/sources-manifest.json",
            merged_transcription_path="inputs/merged.json",
        )
    with pytest.raises(ContractViolation, match="managed path"):
        RunnerRequest(
            CONTRACT_VERSION,
            "job_1",
            1,
            "merged_transcription",
            "inputs/sources-manifest.json",
            merged_transcription_path="inputs/merged.json",
        )
    merged_request = RunnerRequest(
        CONTRACT_VERSION,
        "job_1",
        1,
        "merged_transcription",
        merged_transcription_path="inputs/merged.json",
    )
    assert merged_request.source_manifest_path is None


def test_event_round_trip_is_primitive_json() -> None:
    event = RunnerEvent(
        CONTRACT_VERSION,
        EventType.STAGE_PROGRESS,
        3,
        StageCode.TRANSCRIPTION,
        current_ratio=0.5,
        parameters={"batch": 2},
    )
    payload = event.to_payload()
    assert json.loads(json.dumps(payload)) == payload
    assert validate_ipc_event(payload) == event


@pytest.mark.parametrize(
    "payload",
    [
        {"contract_version": CONTRACT_VERSION, "event_type": "unknown"},
        {
            "contract_version": CONTRACT_VERSION,
            "event_type": "run_completed",
            "revision": 1,
            "stage_code": None,
            "substage_code": None,
            "current_ratio": None,
            "overall_ratio": None,
            "estimate_seconds": None,
            "code": None,
            "parameters": {},
            "secret": "nope",
        },
    ],
)
def test_unknown_events_and_fields_are_rejected(payload: object) -> None:
    with pytest.raises(ContractViolation):
        validate_ipc_event(payload)


def test_event_codes_and_required_fields_are_deny_by_default() -> None:
    with pytest.raises(ContractViolation, match="unknown stage_code"):
        RunnerEvent(
            CONTRACT_VERSION,
            EventType.STAGE_STARTED,
            1,
            stage_code="invented_stage",  # type: ignore[arg-type]
        )
    with pytest.raises(ContractViolation, match="warning events"):
        RunnerEvent(
            CONTRACT_VERSION,
            EventType.WARNING_RAISED,
            1,
            code="invented_warning",  # type: ignore[arg-type]
        )
    with pytest.raises(ContractViolation, match="stage events require"):
        RunnerEvent(CONTRACT_VERSION, EventType.STAGE_STARTED, 1)
    event = RunnerEvent(
        CONTRACT_VERSION,
        EventType.WARNING_RAISED,
        1,
        code=WarningCode.RETRY_IN_PROGRESS,
    )
    assert event.code == WarningCode.RETRY_IN_PROGRESS


def test_result_requires_metrics_and_known_terminal_statuses() -> None:
    result = RunnerResult(
        CONTRACT_VERSION,
        RunnerStatus.TIMED_OUT,
        metrics=RunnerMetrics(elapsed_seconds=12, input_tokens=42),
        error_code=ErrorCode.TIMEOUT,
    )
    assert result.metrics.input_tokens == 42
    with pytest.raises(ContractViolation, match="known error_code"):
        RunnerResult(
            CONTRACT_VERSION,
            RunnerStatus.TIMED_OUT,
            error_code="timeout",  # type: ignore[arg-type]
        )
    with pytest.raises(ContractViolation, match="timed_out results require timeout"):
        RunnerResult(
            CONTRACT_VERSION,
            RunnerStatus.TIMED_OUT,
            error_code=ErrorCode.PROCESSING_FAILED,
        )
    with pytest.raises(
        ContractViolation, match="cancel_failed results require cancel_failed"
    ):
        RunnerResult(
            CONTRACT_VERSION,
            RunnerStatus.CANCEL_FAILED,
            error_code=ErrorCode.TIMEOUT,
        )


def test_event_rejects_non_finite_json_numbers() -> None:
    with pytest.raises(ContractViolation, match="between 0 and 1"):
        RunnerEvent(
            CONTRACT_VERSION,
            EventType.STAGE_PROGRESS,
            1,
            stage_code=StageCode.TRANSCRIPTION,
            current_ratio=float("nan"),
        )
    with pytest.raises(ContractViolation, match="finite"):
        RunnerEvent(
            CONTRACT_VERSION,
            EventType.WARNING_RAISED,
            1,
            code=WarningCode.RETRY_IN_PROGRESS,
            parameters={"delay": float("inf")},
        )


def test_event_rejects_oversized_payload_and_callbacks() -> None:
    with pytest.raises(ContractViolation, match="maximum size"):
        RunnerEvent(
            CONTRACT_VERSION,
            EventType.WARNING_RAISED,
            1,
            code=WarningCode.RETRY_IN_PROGRESS,
            parameters={f"detail{index}": "x" * 1_024 for index in range(16)},
        )
    with pytest.raises(ContractViolation, match="transport safe"):
        RunnerEvent(
            CONTRACT_VERSION,
            EventType.WARNING_RAISED,
            1,
            code=WarningCode.RETRY_IN_PROGRESS,
            parameters={"callback": lambda: None},
        )  # type: ignore[dict-item]
