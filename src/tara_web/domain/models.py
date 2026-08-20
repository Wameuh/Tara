"""Strict persistence-facing domain values; deliberately independent from HTTP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath, PureWindowsPath

from tara_web.domain.enums import JobStatus, UploadSessionStatus

HMAC = re.compile(r"^v[1-9][0-9]*:[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _opaque(value: str) -> str:
    if (
        not 16 <= len(value) <= 128
        or not value.isascii()
        or not value.replace("_", "").replace("-", "").isalnum()
    ):
        raise ValueError("opaque identifier is invalid")
    return value


def _utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must be UTC")
    return value


def validate_relative_storage_path(value: str) -> str:
    """Accept only backend-managed POSIX relative paths on every host OS."""
    if not 1 <= len(value) <= 512 or "\\" in value or "\x00" in value:
        raise ValueError("storage path is invalid")
    windows = PureWindowsPath(value)
    path = PurePath(value)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in path.parts
    ):
        raise ValueError("storage path is invalid")
    return value


@dataclass(frozen=True, slots=True)
class UploadSession:
    public_id: str
    secret_hmac: str
    expires_at: str
    reserved_bytes: int = 0
    status: UploadSessionStatus = UploadSessionStatus.CREATED
    secret_generation: int = 1

    def __post_init__(self) -> None:
        _opaque(self.public_id)
        if not HMAC.fullmatch(self.secret_hmac):
            raise ValueError("secret digest is invalid")
        _utc(self.expires_at)
        if not isinstance(self.status, UploadSessionStatus):
            raise ValueError("upload session status is invalid")
        if not isinstance(self.reserved_bytes, int) or self.reserved_bytes < 0:
            raise ValueError("reserved bytes must be non-negative")
        if not isinstance(self.secret_generation, int) or self.secret_generation < 1:
            raise ValueError("secret generation is invalid")


@dataclass(frozen=True, slots=True)
class Job:
    public_id: str
    session_public_id: str
    secret_hmac: str
    pipeline_version: str
    expires_at: str
    status: JobStatus = JobStatus.QUEUED
    job_type: str = "audio"
    language: str = "fr"

    def __post_init__(self) -> None:
        _opaque(self.public_id)
        _opaque(self.session_public_id)
        if not HMAC.fullmatch(self.secret_hmac):
            raise ValueError("secret digest is invalid")
        if not isinstance(self.status, JobStatus):
            raise ValueError("job status is invalid")
        if not 1 <= len(self.pipeline_version) <= 128:
            raise ValueError("pipeline version is invalid")
        if self.job_type not in {"audio", "merged_transcription"}:
            raise ValueError("job type is invalid")
        if not 2 <= len(self.language) <= 16 or not self.language.isascii():
            raise ValueError("job language is invalid")
        _utc(self.expires_at)


@dataclass(frozen=True, slots=True)
class JobMetricsRecord:
    """Bounded historical regression sample, never supplied by an HTTP client."""

    job_public_id: str
    input_size_bytes: int = 0
    input_file_count: int = 0
    merged_transcription_size_bytes: int = 0
    merged_transcription_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    audio_duration_ms: int = 0
    transcription_duration_ms: int = 0
    validation_duration_ms: int = 0
    preparation_duration_ms: int = 0
    narrative_analysis_duration_ms: int = 0
    synthesis_duration_ms: int = 0
    verification_duration_ms: int = 0
    provider_cost_micro_eur: int = 0
    attempt_count: int = 1
    failed_attempt_count: int = 0
    cancelled_attempt_count: int = 0

    def __post_init__(self) -> None:
        _opaque(self.job_public_id)
        values = (
            self.input_size_bytes,
            self.input_file_count,
            self.merged_transcription_size_bytes,
            self.merged_transcription_tokens,
            self.input_tokens,
            self.output_tokens,
            self.duration_ms,
            self.audio_duration_ms,
            self.transcription_duration_ms,
            self.validation_duration_ms,
            self.preparation_duration_ms,
            self.narrative_analysis_duration_ms,
            self.synthesis_duration_ms,
            self.verification_duration_ms,
            self.provider_cost_micro_eur,
            self.attempt_count,
            self.failed_attempt_count,
            self.cancelled_attempt_count,
        )
        if any(
            not isinstance(value, int) or value < 0 or value > 10**15
            for value in values
        ):
            raise ValueError("job metrics are invalid")
        if self.attempt_count < 1:
            raise ValueError("job metrics are invalid")
        if (
            self.failed_attempt_count + self.cancelled_attempt_count
            > self.attempt_count
        ):
            raise ValueError("job metrics are invalid")
