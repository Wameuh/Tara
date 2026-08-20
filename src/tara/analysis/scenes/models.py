"""Scene timeline models for Tara's scene-driven blackboard pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from tara.analysis.models import ClaimType, Confidence, JsonObject, TaraModel
from tara.analysis.structured_output import claim_type_from_string


def _coerce_string_list(value: Any, *text_keys: str) -> list[str]:
    """Normalize LLM list fields that may be plain strings or small objects."""
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append(text)
            continue
        if isinstance(item, dict):
            for key in text_keys:
                raw = item.get(key)
                if isinstance(raw, str) and raw.strip():
                    normalized.append(raw.strip())
                    break
    return normalized


class ScenePipelineWarning(TaraModel):
    """Non-fatal issue produced while building scene artifacts."""

    code: str
    message: str
    scene_id: int | None = None
    metadata: JsonObject = Field(default_factory=dict)


class SceneBoundary(TaraModel):
    """A coarse scene boundary identified from the transcript."""

    scene_id: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    summary: str = Field(default="", max_length=2000)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Ensure scene end does not precede start."""
        if self.end < self.start:
            raise ValueError("Scene boundary end must be greater than start.")
        return self


class SceneTranscription(TaraModel):
    """Transcript slice belonging to one scene."""

    scene_id: int = Field(ge=1)
    title: str = ""
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    summary: str = ""
    text: str = ""
    segments: list[dict] = Field(default_factory=list)
    source_hash: str = ""
    output_path: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Ensure scene end does not precede start."""
        if self.end < self.start:
            raise ValueError("Scene transcription end must be greater than start.")
        return self


class SceneFact(TaraModel):
    """Fact extracted from one scene description."""

    claim: str = Field(min_length=1, max_length=2000)
    claim_type: ClaimType = ClaimType.CHRONOLOGY

    @field_validator("claim_type", mode="before")
    @classmethod
    def coerce_claim_type(cls, value: Any) -> Any:
        """Map loose LLM claim-type strings to :class:`ClaimType`."""
        if isinstance(value, ClaimType):
            return value
        if isinstance(value, str):
            mapped = claim_type_from_string(value)
            if mapped is not None:
                return mapped
        return value
    confidence: Confidence = Confidence.MEDIUM
    importance: int = Field(default=3, ge=1, le=5)
    is_critical: bool = False
    supporting_segment_ids: list[int] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class SceneDescription(TaraModel):
    """Long scene description and structured facts for blackboard ingestion."""

    scene_id: int = Field(ge=1)
    title: str = Field(max_length=200)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    summary: str = Field(max_length=2000)
    description: str = Field(max_length=32_000)
    facts: list[SceneFact] = Field(default_factory=list)
    key_actions: list[str] = Field(default_factory=list)
    state_changes: list[str] = Field(default_factory=list)
    continuity_impacts: list[str] = Field(default_factory=list)
    source_hash: str = ""
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("key_actions", mode="before")
    @classmethod
    def coerce_key_actions(cls, value: Any) -> list[str]:
        """Accept plain strings or ``{action: ...}`` objects from the LLM."""
        return _coerce_string_list(value, "action", "text", "description")

    @field_validator("state_changes", mode="before")
    @classmethod
    def coerce_state_changes(cls, value: Any) -> list[str]:
        """Accept plain strings or ``{change: ...}`` objects from the LLM."""
        return _coerce_string_list(value, "change", "text", "description")

    @field_validator("continuity_impacts", mode="before")
    @classmethod
    def coerce_continuity_impacts(cls, value: Any) -> list[str]:
        """Accept plain strings or ``{impact: ...}`` objects from the LLM."""
        return _coerce_string_list(value, "impact", "text", "description")

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Ensure scene end does not precede start."""
        if self.end < self.start:
            raise ValueError("Scene description end must be greater than start.")
        return self


class SceneTimeline(TaraModel):
    """Ordered collection of scene descriptions used by composer and audit."""

    scenes: list[SceneDescription] = Field(default_factory=list)
    warnings: list[ScenePipelineWarning] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ordering(self) -> Self:
        """Ensure scene ids are unique and sorted by time."""
        ids = [scene.scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Scene timeline requires unique scene ids.")
        return self


class ScenePipelineResult(TaraModel):
    """Artifacts produced by one scene analysis pipeline run."""

    timeline: SceneTimeline
    scene_analysis_path: str | None = None
    scene_descriptions_path: str | None = None
    scenes_dir: str | None = None
    warnings: list[ScenePipelineWarning] = Field(default_factory=list)
    scene_llm_call_count: int = Field(default=0, ge=0)
    estimated_scene_llm_tokens: int = Field(default=0, ge=0)
    estimated_scene_cost_usd: float | None = Field(default=None, ge=0.0)

    @property
    def scene_count(self) -> int:
        """Return the number of described scenes."""
        return len(self.timeline.scenes)


def path_to_str(path: Path | None) -> str | None:
    """Serialize an optional path for scene artifacts."""
    return str(path) if path is not None else None
