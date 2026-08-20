"""Explicit schema adapters; no inferred migrations are permitted."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from tara.schemas.common import PortableMetadata, StrictSchema
from tara.schemas.merged_transcription import (
    MergedTranscription,
    MergedTranscriptionContent,
    TranscriptionSegment,
)
from tara.schemas.public_result import PublicResult, publish_internal_result


class LegacyMergedV0(StrictSchema):
    """The only supported non-versioned merged-transcription form."""

    text: str
    segments: list[dict[str, Any]] = Field(default_factory=list)
    language: str | None = None
    duration: float | None = None
    model: str | None = None


class LegacySummaryV1(StrictSchema):
    title: str
    sections: list[LegacySectionV1] = Field(default_factory=list)
    scenes: list[object] = Field(default_factory=list, max_length=0)


class LegacySectionV1(StrictSchema):
    """Only the Task08 section fields consumed by the v1 adapter."""

    section_id: str | None = None
    title: str
    content: str


class LegacyFinalYamlV1(StrictSchema):
    schema_version: Literal[1]
    summary: LegacySummaryV1


def adapt_legacy_merged_v0(value: dict[str, Any]) -> MergedTranscription:
    legacy = LegacyMergedV0.model_validate(value)
    segments = [TranscriptionSegment.model_validate(item) for item in legacy.segments]
    return MergedTranscription(
        content=MergedTranscriptionContent(
            text=legacy.text,
            segments=segments,
            duration=legacy.duration,
            model=legacy.model,
        ),
        metadata=PortableMetadata(language=legacy.language, producer="legacy"),
    )


def adapt_final_yaml_v1(value: dict[str, Any]) -> PublicResult:
    legacy = LegacyFinalYamlV1.model_validate(value)
    sections = [
        section.model_dump(exclude_none=True) for section in legacy.summary.sections
    ] or [
        {
            "section_id": "executive_summary",
            "title": legacy.summary.title,
            "content": legacy.summary.title,
        }
    ]
    return publish_internal_result(legacy.summary.title, sections)


ADAPTERS = {
    ("tara.merged_transcription", "legacy-v0"): adapt_legacy_merged_v0,
    ("tara.public_result", 1): adapt_final_yaml_v1,
}
