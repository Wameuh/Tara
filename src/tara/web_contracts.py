# ruff: noqa: E501
# ruff: noqa: E501, E701
"""Versioned, transport-safe contracts between Tara and the web orchestrator.

The module deliberately depends only on the standard library: a worker can
import it without importing FastAPI, SQLite, or any web application code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from math import isfinite
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final, Protocol

CONTRACT_VERSION: Final = 1
MAX_IPC_EVENT_BYTES: Final = 16 * 1024
MAX_IPC_EVENTS_PER_RESULT: Final = 256
MAX_IPC_ARTIFACTS: Final = 32
MAX_IPC_PARAMETERS: Final = 16
MAX_IPC_STRING_LENGTH: Final = 1_024
MAX_TARA_CONFIG_SNAPSHOT_BYTES: Final = 65_536


class ContractViolation(ValueError):
    """Raised when an untrusted worker message violates the IPC contract."""


class EventType(StrEnum):
    STAGE_STARTED = "stage_started"
    STAGE_PROGRESS = "stage_progress"
    STAGE_COMPLETED = "stage_completed"
    RETRY_SCHEDULED = "retry_scheduled"
    WARNING_RAISED = "warning_raised"
    USAGE_RECORDED = "usage_recorded"
    ARTIFACT_DECLARED = "artifact_declared"
    CANCELLATION_ACKNOWLEDGED = "cancellation_acknowledged"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class StageCode(StrEnum):
    QUEUED = "queued"
    INPUT_VALIDATION = "input_validation"
    TRANSCRIPTION = "transcription"
    SESSION_PREPARATION = "session_preparation"
    NARRATIVE_ANALYSIS = "narrative_analysis"
    SYNTHESIS = "synthesis"
    VERIFICATION = "verification"
    RESULT_READY = "result_ready"


class WarningCode(StrEnum):
    AUDIO_DURATION_HIGH = "audio_duration_high"
    RETRY_IN_PROGRESS = "retry_in_progress"
    ARTIFACT_MISSING = "artifact_missing"
    SERVICE_DEGRADED = "service_degraded"


class ErrorCode(StrEnum):
    INPUT_INVALID = "input_invalid"
    INPUT_TOO_LARGE = "input_too_large"
    TRANSCRIPTION_FAILED = "transcription_failed"
    PROCESSING_FAILED = "processing_failed"
    TIMEOUT = "timeout"
    CANCEL_FAILED = "cancel_failed"
    SERVER_INTERRUPTED = "server_interrupted"
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"
    RESULT_INTEGRITY_FAILED = "result_integrity_failed"


class ArtifactType(StrEnum):
    AUDIO_INPUT = "audio_input"
    MERGED_TRANSCRIPTION_INPUT = "merged_transcription_input"
    CONTEXT_INPUT = "context_input"
    PREVIOUS_SUMMARY_INPUT = "previous_summary_input"
    RAW_TRANSCRIPTION = "raw_transcription"
    SCENE_DATA = "scene_data"
    BLACKBOARD = "blackboard"
    PROMPT = "prompt"
    CACHE = "cache"
    FINAL_YAML = "final_yaml"
    TECHNICAL_ERROR = "technical_error"


class RunnerStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CANCEL_FAILED = "cancel_failed"


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    max_context_tokens: int = 2_000
    max_previous_summaries_tokens: int = 50_000
    max_merged_transcription_tokens: int = 500_000
    max_event_bytes: int = MAX_IPC_EVENT_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_context_tokens", self.max_context_tokens),
            ("max_previous_summaries_tokens", self.max_previous_summaries_tokens),
            ("max_merged_transcription_tokens", self.max_merged_transcription_tokens),
            ("max_event_bytes", self.max_event_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ContractViolation(f"{name} must be a positive integer")
        if self.max_event_bytes > MAX_IPC_EVENT_BYTES:
            raise ContractViolation("max_event_bytes exceeds the contract maximum")


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    """Immutable worker input. It contains no job secret or database handle."""

    contract_version: int
    job_id: str
    attempt_number: int
    input_kind: str
    source_manifest_path: str | None = None
    context_path: str | None = None
    previous_summaries_path: str | None = None
    merged_transcription_path: str | None = None
    language: str = "fr"
    limits: RunnerLimits = field(default_factory=RunnerLimits)
    tara_config_json: str | None = None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractViolation("unsupported runner contract version")
        _require_identifier(self.job_id, "job_id")
        if (
            not isinstance(self.attempt_number, int)
            or isinstance(self.attempt_number, bool)
            or self.attempt_number < 1
        ):
            raise ContractViolation("attempt_number must be a positive integer")
        if self.input_kind not in {"audio", "merged_transcription"}:
            raise ContractViolation("unknown input_kind")
        if self.input_kind == "audio":
            if self.source_manifest_path is None:
                raise ContractViolation(
                    "audio requests require a managed source manifest"
                )
            if self.merged_transcription_path is not None:
                raise ContractViolation(
                    "audio requests forbid a merged transcription path"
                )
            _require_relative_path(self.source_manifest_path)
        if self.input_kind == "merged_transcription":
            if (
                self.source_manifest_path is not None
                or self.merged_transcription_path is None
            ):
                raise ContractViolation(
                    "merged transcription requests require one managed path"
                )
            _require_relative_path(self.merged_transcription_path)
        for _name, value in (
            ("context_path", self.context_path),
            ("previous_summaries_path", self.previous_summaries_path),
        ):
            if value is not None:
                _require_relative_path(value)
        _require_identifier(self.language, "language")
        if not isinstance(self.limits, RunnerLimits):
            raise ContractViolation("limits must be RunnerLimits")
        if self.tara_config_json is not None:
            import json

            if (
                not isinstance(self.tara_config_json, str)
                or len(self.tara_config_json.encode("utf-8"))
                > MAX_TARA_CONFIG_SNAPSHOT_BYTES
            ):
                raise ContractViolation("Tara configuration snapshot is invalid")
            try:
                snapshot = json.loads(self.tara_config_json)
            except (TypeError, ValueError) as exc:
                raise ContractViolation(
                    "Tara configuration snapshot is invalid"
                ) from exc
            if not isinstance(snapshot, dict) or not set(snapshot).issubset(
                {"language", "logging", "transcription", "processing", "analysis"}
            ):
                raise ContractViolation("Tara configuration snapshot is invalid")
            _assert_transport_safe(snapshot)

    def to_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "job_id": self.job_id,
            "attempt_number": self.attempt_number,
            "input_kind": self.input_kind,
            "source_manifest_path": self.source_manifest_path,
            "context_path": self.context_path,
            "previous_summaries_path": self.previous_summaries_path,
            "merged_transcription_path": self.merged_transcription_path,
            "language": self.language,
            "limits": asdict(self.limits),
            "tara_config_json": self.tara_config_json,
        }


def runner_request_from_payload(payload: object) -> RunnerRequest:
    if not isinstance(payload, dict) or set(payload) != {
        "contract_version",
        "job_id",
        "attempt_number",
        "input_kind",
        "source_manifest_path",
        "context_path",
        "previous_summaries_path",
        "merged_transcription_path",
        "language",
        "limits",
        "tara_config_json",
    }:
        raise ContractViolation("runner request shape is invalid")
    try:
        limits = payload["limits"]
        if not isinstance(limits, dict):
            raise ValueError
        return RunnerRequest(**payload | {"limits": RunnerLimits(**limits)})
    except (TypeError, ValueError) as exc:
        raise ContractViolation("runner request is invalid") from exc


@dataclass(frozen=True, slots=True)
class RunnerEvent:
    """A bounded event that can be converted to JSON without custom hooks."""

    contract_version: int
    event_type: EventType
    revision: int
    stage_code: StageCode | None = None
    substage_code: str | None = None
    current_ratio: float | None = None
    overall_ratio: float | None = None
    estimate_seconds: int | None = None
    code: WarningCode | ErrorCode | ArtifactType | None = None
    parameters: Mapping[str, str | int | float | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractViolation("unsupported event contract version")
        if not isinstance(self.event_type, EventType):
            raise ContractViolation("unknown event type")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 0
        ):
            raise ContractViolation("revision must be a non-negative integer")
        if self.stage_code is not None and not isinstance(self.stage_code, StageCode):
            raise ContractViolation("unknown stage_code")
        if self.substage_code is not None:
            _require_identifier(self.substage_code, "substage_code")
        self._validate_event_fields()
        for name, value in (
            ("current_ratio", self.current_ratio),
            ("overall_ratio", self.overall_ratio),
        ):
            if value is not None and (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ContractViolation(f"{name} must be between 0 and 1")
        if self.estimate_seconds is not None and (
            not isinstance(self.estimate_seconds, int) or self.estimate_seconds < 0
        ):
            raise ContractViolation("estimate_seconds must be non-negative")
        _validate_parameters(self.parameters)
        self.to_payload()  # Enforce the byte limit at construction time.

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["event_type"] = self.event_type.value
        if self.stage_code is not None:
            payload["stage_code"] = self.stage_code.value
        if self.code is not None:
            payload["code"] = self.code.value
        _assert_transport_safe(payload)
        import json

        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        if len(encoded) > MAX_IPC_EVENT_BYTES:
            raise ContractViolation("IPC event exceeds the maximum size")
        return payload

    def _validate_event_fields(self) -> None:
        stage_events = {
            EventType.STAGE_STARTED,
            EventType.STAGE_PROGRESS,
            EventType.STAGE_COMPLETED,
            EventType.RETRY_SCHEDULED,
        }
        if self.event_type in stage_events and self.stage_code is None:
            raise ContractViolation("stage events require a known stage_code")
        if self.event_type == EventType.WARNING_RAISED:
            if not isinstance(self.code, WarningCode):
                raise ContractViolation("warning events require a known warning code")
        elif self.event_type == EventType.RETRY_SCHEDULED:
            if self.code not in {None, WarningCode.RETRY_IN_PROGRESS}:
                raise ContractViolation("retry events only accept retry_in_progress")
        elif self.event_type == EventType.ARTIFACT_DECLARED:
            if not isinstance(self.code, ArtifactType):
                raise ContractViolation("artifact events require a known artifact type")
        elif self.event_type == EventType.RUN_FAILED:
            if not isinstance(self.code, ErrorCode):
                raise ContractViolation("failed runs require a known error code")
        elif self.code is not None:
            raise ContractViolation("event type does not accept a code")


@dataclass(frozen=True, slots=True)
class RunnerArtifact:
    artifact_type: ArtifactType
    relative_path: str
    required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_type, ArtifactType):
            raise ContractViolation("unknown artifact_type")
        _require_relative_path(self.relative_path)
        if not isinstance(self.required, bool):
            raise ContractViolation("required must be a boolean")


@dataclass(frozen=True, slots=True)
class RunnerMetrics:
    elapsed_seconds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("elapsed_seconds", self.elapsed_seconds),
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cost_micros", self.cost_micros),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ContractViolation(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class RunnerResult:
    contract_version: int
    status: RunnerStatus
    metrics: RunnerMetrics = field(default_factory=RunnerMetrics)
    error_code: ErrorCode | None = None
    artifacts: tuple[RunnerArtifact, ...] = ()

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractViolation("unsupported result contract version")
        if not isinstance(self.status, RunnerStatus):
            raise ContractViolation("unknown result status")
        if self.status in {
            RunnerStatus.FAILED,
            RunnerStatus.TIMED_OUT,
            RunnerStatus.CANCEL_FAILED,
        }:
            if not isinstance(self.error_code, ErrorCode):
                raise ContractViolation("failed results require a known error_code")
        elif self.error_code is not None:
            raise ContractViolation("only failed results may include an error_code")
        expected_error_codes = {
            RunnerStatus.TIMED_OUT: ErrorCode.TIMEOUT,
            RunnerStatus.CANCEL_FAILED: ErrorCode.CANCEL_FAILED,
        }
        if (
            self.status in expected_error_codes
            and self.error_code != expected_error_codes[self.status]
        ):
            raise ContractViolation(
                f"{self.status.value} results require {expected_error_codes[self.status].value}"
            )
        if not isinstance(self.metrics, RunnerMetrics):
            raise ContractViolation("metrics must be RunnerMetrics")
        if (
            not isinstance(self.artifacts, tuple)
            or len(self.artifacts) > MAX_IPC_ARTIFACTS
        ):
            raise ContractViolation("artifacts must be a bounded tuple")


class EventSink(Protocol):
    def emit(self, event: RunnerEvent) -> None: ...


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


class TaraWebRunner(Protocol):
    def run(
        self,
        request: RunnerRequest,
        event_sink: EventSink,
        cancellation_token: CancellationToken,
    ) -> RunnerResult: ...


def validate_ipc_event(payload: object) -> RunnerEvent:
    """Parse a primitive IPC payload; unknown keys and event types are rejected."""
    if not isinstance(payload, dict):
        raise ContractViolation("IPC event must be an object")
    allowed = {field.name for field in __import__("dataclasses").fields(RunnerEvent)}
    if set(payload) != allowed:
        raise ContractViolation("IPC event fields do not match the contract")
    try:
        return RunnerEvent(
            contract_version=payload["contract_version"],
            event_type=EventType(payload["event_type"]),
            revision=payload["revision"],
            stage_code=(
                StageCode(payload["stage_code"])
                if payload["stage_code"] is not None
                else None
            ),
            substage_code=payload["substage_code"],
            current_ratio=payload["current_ratio"],
            overall_ratio=payload["overall_ratio"],
            estimate_seconds=payload["estimate_seconds"],
            code=_parse_event_code(payload["event_type"], payload["code"]),
            parameters=payload["parameters"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractViolation("invalid IPC event") from exc


def _require_identifier(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value.replace("_", "").replace("-", "").isalnum()
    ):
        raise ContractViolation(f"{name} must be a short identifier")


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) > MAX_IPC_STRING_LENGTH:
        raise ContractViolation(f"{name} must be bounded text")


def _require_relative_path(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ContractViolation("relative_path must be bounded")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in path.parts
        or "\\" in value
    ):
        raise ContractViolation("relative_path must stay within managed storage")


def _validate_parameters(parameters: object) -> None:
    if not isinstance(parameters, Mapping) or len(parameters) > MAX_IPC_PARAMETERS:
        raise ContractViolation("parameters must be a bounded mapping")
    for key, value in parameters.items():
        _require_identifier(key, "parameter name")
        if not isinstance(value, str | int | float | bool):
            raise ContractViolation("parameter value is not transport safe")
        if isinstance(value, str) and len(value) > MAX_IPC_STRING_LENGTH:
            raise ContractViolation("parameter value is not transport safe")
        if isinstance(value, float) and not isfinite(value):
            raise ContractViolation("parameter value must be finite")


def _assert_transport_safe(value: object) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        return
    if isinstance(value, list) or isinstance(value, tuple):
        for item in value:
            _assert_transport_safe(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractViolation("IPC object keys must be strings")
            _assert_transport_safe(item)
        return
    if is_dataclass(value) or callable(value) or isinstance(value, BaseException):
        raise ContractViolation("IPC payload contains a forbidden object")
    raise ContractViolation("IPC payload is not serializable")


def _parse_event_code(
    event_type: object, code: object
) -> WarningCode | ErrorCode | ArtifactType | None:
    if code is None:
        return None
    if event_type == EventType.WARNING_RAISED.value:
        return WarningCode(code)
    if event_type == EventType.ARTIFACT_DECLARED.value:
        return ArtifactType(code)
    if event_type == EventType.RUN_FAILED.value:
        return ErrorCode(code)
    if event_type == EventType.RETRY_SCHEDULED.value:
        return WarningCode(code)
    raise ContractViolation("event type does not accept a code")
