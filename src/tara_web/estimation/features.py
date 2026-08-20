"""Bounded model inputs computed from validated server-side metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EstimationFeatures:
    job_type: str
    pipeline_version: str
    audio_duration_ms: int = 0
    merged_transcription_tokens: int = 0
    input_file_count: int = 0

    def __post_init__(self) -> None:
        if self.job_type not in {"audio", "merged_transcription"}:
            raise ValueError("estimation job type is invalid")
        if not 1 <= len(self.pipeline_version) <= 128:
            raise ValueError("estimation pipeline version is invalid")
        if any(
            isinstance(value, bool) or not 0 <= value <= 10**15
            for value in (
                self.audio_duration_ms,
                self.merged_transcription_tokens,
                self.input_file_count,
            )
        ):
            raise ValueError("estimation features are invalid")

    @property
    def primary_value(self) -> int:
        return (
            self.audio_duration_ms
            if self.job_type == "audio"
            else self.merged_transcription_tokens
        )
