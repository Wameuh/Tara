"""Versioned canonical merged-transcription schema."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, PortableMetadata, StrictSchema

SCHEMA_NAME = "tara.merged_transcription"
MAX_TRANSCRIPTION_TEXT_CHARS = 32 * 1024 * 1024


class SegmentAuthor(StrictSchema):
    speaker: Annotated[str, Field(min_length=1, max_length=128)]
    source_file: Annotated[str, Field(min_length=1, max_length=255)]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TranscriptionSegment(StrictSchema):
    start: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    end: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    text: Annotated[str, Field(max_length=MAX_TRANSCRIPTION_TEXT_CHARS)]
    author: SegmentAuthor | None = None

    @model_validator(mode="after")
    def ordered(self) -> TranscriptionSegment:
        if self.end < self.start:
            raise ValueError("Segment end must be greater than or equal to start.")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class MergedTranscriptionContent(StrictSchema):
    text: Annotated[str, Field(max_length=MAX_TRANSCRIPTION_TEXT_CHARS)]
    segments: list[TranscriptionSegment] = Field(max_length=50_000)
    duration: Annotated[float | None, Field(default=None, ge=0, allow_inf_nan=False)]
    model: Annotated[str | None, Field(default=None, max_length=256)]


class MergedTranscription(StrictSchema):
    schema_name: Literal[SCHEMA_NAME] = SCHEMA_NAME
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    metadata: PortableMetadata = Field(default_factory=PortableMetadata)
    content: MergedTranscriptionContent

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_yaml(cls, path: Path) -> MergedTranscription:
        from .registry import load_merged_transcription

        return load_merged_transcription(path)

    @property
    def text(self) -> str:
        return self.content.text

    @property
    def segments(self) -> list[TranscriptionSegment]:
        return self.content.segments

    @property
    def language(self) -> str | None:
        return self.metadata.language

    @property
    def duration(self) -> float | None:
        return self.content.duration

    @property
    def model(self) -> str | None:
        return self.content.model


def new_merged_transcription(
    *,
    text: str,
    segments: list[TranscriptionSegment],
    language: str | None = None,
    duration: float | None = None,
    model: str | None = None,
    producer: str | None = None,
) -> MergedTranscription:
    """Build the current strict envelope for in-memory Tara producers."""
    return MergedTranscription(
        metadata=PortableMetadata(language=language, producer=producer),
        content=MergedTranscriptionContent(
            text=text,
            segments=segments,
            duration=duration,
            model=model,
        ),
    )
