"""Bounded JSON-only messages from inference workers to their parent."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

MAX_INFERENCE_IPC_BYTES = 64 * 1024 * 1024
MAX_SEGMENTS = 100_000
MAX_TEXT_CHARS = 16 * 1024 * 1024


class InferenceIpcViolation(ValueError):
    """A worker emitted a malformed, oversized, or non-JSON message."""


def encode_worker_message(message: Mapping[str, object]) -> bytes:
    """Validate and encode one worker message without pickle."""
    normalized = _validate_message(message)
    try:
        raw = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InferenceIpcViolation("worker message is not JSON") from exc
    if len(raw) > MAX_INFERENCE_IPC_BYTES:
        raise InferenceIpcViolation("worker message exceeds the maximum size")
    return raw


def decode_worker_message(data: object) -> dict[str, object]:
    """Decode one bounded JSON worker message into validated primitives."""
    if not isinstance(data, bytes) or len(data) > MAX_INFERENCE_IPC_BYTES:
        raise InferenceIpcViolation("worker frame is invalid")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InferenceIpcViolation("worker frame is invalid") from exc
    if not isinstance(value, dict):
        raise InferenceIpcViolation("worker message must be an object")
    return _validate_message(value)


def _validate_message(message: Mapping[str, object]) -> dict[str, object]:
    message_type = message.get("type")
    if message_type == "error":
        _exact_keys(message, {"type", "message"})
        _text(message.get("message"), "error message", 4_096)
    elif message_type == "segment":
        _exact_keys(message, {"type", "text", "start", "end", "progress"})
        _text(message.get("text"), "segment text", MAX_TEXT_CHARS)
        _number(message.get("start"), "segment start")
        _number(message.get("end"), "segment end")
        if message.get("progress") is not None:
            progress = _number(message.get("progress"), "segment progress")
            if not 0 <= progress <= 100:
                raise InferenceIpcViolation("segment progress is invalid")
    elif message_type == "final" and "payload" in message:
        _exact_keys(message, {"type", "payload"})
        payload = message.get("payload")
        if not isinstance(payload, Mapping):
            raise InferenceIpcViolation("final payload is invalid")
        _validate_final_payload(payload)
    elif message_type == "final":
        _exact_keys(message, {"type", "text", "language", "duration", "model"})
        _text(message.get("text"), "final text", MAX_TEXT_CHARS)
        _optional_text(message.get("language"), "language", 128)
        _optional_number(message.get("duration"), "duration")
        _text(message.get("model"), "model", 512)
    else:
        raise InferenceIpcViolation("worker message type is invalid")
    return dict(message)


def _validate_final_payload(payload: Mapping[str, object]) -> None:
    _exact_keys(payload, {"text", "segments", "language", "duration", "model"})
    _text(payload.get("text"), "final text", MAX_TEXT_CHARS)
    _optional_text(payload.get("language"), "language", 128)
    _optional_number(payload.get("duration"), "duration")
    _text(payload.get("model"), "model", 512)
    segments = payload.get("segments")
    if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
        raise InferenceIpcViolation("final segments are invalid")
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise InferenceIpcViolation("final segment is invalid")
        _exact_keys(segment, {"start", "end", "text"})
        _number(segment.get("start"), "segment start")
        _number(segment.get("end"), "segment end")
        _text(segment.get("text"), "segment text", MAX_TEXT_CHARS)


def _exact_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise InferenceIpcViolation("worker message shape is invalid")


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise InferenceIpcViolation(f"{label} is invalid")
    return value


def _optional_text(value: object, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum)


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InferenceIpcViolation(f"{label} is invalid")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise InferenceIpcViolation(f"{label} is invalid")
    return normalized


def _optional_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    return _number(value, label)
