"""Strict JSON-only boundary between an untrusted worker and SQLite owner."""

from __future__ import annotations

import json
from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import Lock

from tara.web_contracts import (
    CONTRACT_VERSION,
    MAX_IPC_ARTIFACTS,
    MAX_IPC_EVENT_BYTES,
    ArtifactType,
    ErrorCode,
    EventType,
    RunnerArtifact,
    RunnerEvent,
    RunnerMetrics,
    RunnerResult,
    RunnerStatus,
    validate_ipc_event,
)


class IpcViolation(ValueError):
    """A worker payload is not a valid, bounded primitive envelope."""


def encode_frame(kind: str, payload: dict[str, object]) -> bytes:
    if kind not in {"event", "result"}:
        raise IpcViolation("IPC frame kind is invalid")
    value = _encoded({"kind": kind, "payload": payload}, "IPC frame")
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def parse_frame(data: object) -> tuple[str, dict[str, object]]:
    if not isinstance(data, bytes) or len(data) > MAX_IPC_EVENT_BYTES:
        raise IpcViolation("IPC frame is invalid")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IpcViolation("IPC frame is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "payload"}
        or value["kind"] not in {"event", "result"}
        or not isinstance(value["payload"], dict)
    ):
        raise IpcViolation("IPC frame is invalid")
    _encoded(value, "IPC frame")
    return value["kind"], value["payload"]


def _encoded(value: dict[str, object], label: str) -> dict[str, object]:
    try:
        raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IpcViolation(f"{label} is not JSON") from exc
    if len(raw) > MAX_IPC_EVENT_BYTES:
        raise IpcViolation(f"{label} exceeds the maximum size")
    return value


def _identifier(value: object, label: str, minimum: int) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= 128
        or not value.isascii()
    ):
        raise IpcViolation(f"IPC {label} is invalid")
    return value


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise IpcViolation(f"IPC {label} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class IpcMessage:
    job_id: str
    attempt_number: int
    worker_token: str
    event: RunnerEvent

    def payload(self) -> dict[str, object]:
        return _encoded(
            {
                "job_id": self.job_id,
                "attempt_number": self.attempt_number,
                "worker_token": self.worker_token,
                "event": self.event.to_payload(),
            },
            "IPC message",
        )


def parse_message(payload: object) -> IpcMessage:
    if not isinstance(payload, dict) or set(payload) != {
        "job_id",
        "attempt_number",
        "worker_token",
        "event",
    }:
        raise IpcViolation("IPC message shape is invalid")
    _encoded(payload, "IPC message")
    job_id = _identifier(payload["job_id"], "job identifier", 16)
    attempt = _positive_int(payload["attempt_number"], "attempt")
    token = _identifier(payload["worker_token"], "worker token", 32)
    try:
        event = validate_ipc_event(payload["event"])
    except ValueError as exc:
        raise IpcViolation("IPC event is invalid") from exc
    return IpcMessage(job_id, attempt, token, event)


@dataclass(frozen=True, slots=True)
class ResultPayload:
    job_id: str
    attempt_number: int
    worker_token: str
    result: RunnerResult


class _ResultCodec:
    _result_keys = {"contract_version", "status", "metrics", "error_code", "artifacts"}
    _metric_keys = {"elapsed_seconds", "input_tokens", "output_tokens", "cost_micros"}
    _artifact_keys = {"artifact_type", "relative_path", "required"}

    def __call__(
        self, job_id: str, attempt: int, token: str, result: RunnerResult
    ) -> dict[str, object]:
        return _encoded(
            {
                "job_id": job_id,
                "attempt_number": attempt,
                "worker_token": token,
                "result": {
                    "contract_version": result.contract_version,
                    "status": result.status.value,
                    "metrics": {
                        "elapsed_seconds": result.metrics.elapsed_seconds,
                        "input_tokens": result.metrics.input_tokens,
                        "output_tokens": result.metrics.output_tokens,
                        "cost_micros": result.metrics.cost_micros,
                    },
                    "error_code": result.error_code.value
                    if result.error_code
                    else None,
                    "artifacts": [
                        {
                            "artifact_type": artifact.artifact_type.value,
                            "relative_path": artifact.relative_path,
                            "required": artifact.required,
                        }
                        for artifact in result.artifacts
                    ],
                },
            },
            "result",
        )

    def parse(self, payload: object) -> ResultPayload:
        if not isinstance(payload, dict) or set(payload) != {
            "job_id",
            "attempt_number",
            "worker_token",
            "result",
        }:
            raise IpcViolation("result shape is invalid")
        _encoded(payload, "result")
        job_id = _identifier(payload["job_id"], "job identifier", 16)
        attempt = _positive_int(payload["attempt_number"], "attempt")
        token = _identifier(payload["worker_token"], "worker token", 32)
        result = payload["result"]
        if not isinstance(result, dict) or set(result) != self._result_keys:
            raise IpcViolation("result shape is invalid")
        if result["contract_version"] != CONTRACT_VERSION or isinstance(
            result["contract_version"], bool
        ):
            raise IpcViolation("result contract version is invalid")
        metrics = result["metrics"]
        if not isinstance(metrics, dict) or set(metrics) != self._metric_keys:
            raise IpcViolation("result metrics are invalid")
        for value in metrics.values():
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise IpcViolation("result metrics are invalid")
        artifacts = result["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) > MAX_IPC_ARTIFACTS:
            raise IpcViolation("result artifacts are invalid")
        parsed_artifacts: list[RunnerArtifact] = []
        try:
            for artifact in artifacts:
                if (
                    not isinstance(artifact, dict)
                    or set(artifact) != self._artifact_keys
                ):
                    raise ValueError
                if not isinstance(artifact["required"], bool):
                    raise ValueError
                parsed_artifacts.append(
                    RunnerArtifact(
                        ArtifactType(artifact["artifact_type"]),
                        artifact["relative_path"],
                        artifact["required"],
                    )
                )
            parsed = RunnerResult(
                CONTRACT_VERSION,
                RunnerStatus(result["status"]),
                RunnerMetrics(**metrics),
                None
                if result["error_code"] is None
                else ErrorCode(result["error_code"]),
                tuple(parsed_artifacts),
            )
        except (TypeError, ValueError) as exc:
            raise IpcViolation("result is invalid") from exc
        return ResultPayload(job_id, attempt, token, parsed)


result_payload = _ResultCodec()


class BoundedIpcBuffer:
    """Coalesce only the latest progress item per job and stage."""

    def __init__(self, maximum: int = 512) -> None:
        if maximum < 1:
            raise ValueError("IPC buffer limit is invalid")
        self.maximum = maximum
        self._items: deque[dict[str, object]] = deque()
        self._progress: OrderedDict[tuple[str, str], dict[str, object]] = OrderedDict()
        self._lock = Lock()

    def put(self, message: IpcMessage) -> bool:
        payload = message.payload()
        with self._lock:
            if message.event.event_type == EventType.STAGE_PROGRESS:
                key = (message.job_id, message.event.stage_code.value)
                self._progress[key] = payload
                return True
            self._flush_progress()
            self._items.append(payload)
            return True

    def drain(self) -> list[dict[str, object]]:
        with self._lock:
            self._flush_progress()
            values = list(self._items)
            self._items.clear()
            return values

    def _flush_progress(self) -> None:
        while self._progress and len(self._items) < self.maximum:
            _, value = self._progress.popitem(last=False)
            self._items.append(value)
