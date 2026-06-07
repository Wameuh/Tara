"""LLM-backed scene boundary detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import Field

from tara.analysis.agentic_llm import LLMUsageDelta, _merge_usage, _response_usage
from tara.analysis.llm_runner import LLMRequest, LLMRunner, LLMRunnerError
from tara.analysis.models import MergedTranscription, TaraModel, TranscriptionSegment
from tara.analysis.scenes.models import SceneBoundary, ScenePipelineWarning
from tara.analysis.scenes.prompts import (
    BOUNDARY_SYSTEM_PROMPT,
    boundary_merge_user_prompt,
    boundary_user_prompt,
)
from tara.analysis.structured_output import parse_typed_json_lenient

LOGGER = logging.getLogger(__name__)


class _BoundaryPayload(TaraModel):
    """Structured response expected from boundary prompts."""

    scenes: list[SceneBoundary] = Field(default_factory=list)


@dataclass(slots=True)
class BoundaryResult:
    """Boundary detection result and usage accounting."""

    boundaries: list[SceneBoundary]
    warnings: list[ScenePipelineWarning]
    usage: LLMUsageDelta


class SceneBoundaryAgent:
    """Identify narrative scene boundaries using Tara's shared LLM runner."""

    def __init__(
        self,
        llm_runner: LLMRunner,
        *,
        block_seconds: float = 1800.0,
    ) -> None:
        """Initialize the boundary detector."""
        self._llm_runner = llm_runner
        self._block_seconds = block_seconds

    def identify_boundaries(
        self,
        transcription: MergedTranscription,
    ) -> BoundaryResult:
        """Identify scene boundaries, falling back to hierarchical blocks."""
        try:
            response = self._llm_runner.run(
                LLMRequest(
                    purpose="analysis.scenes.boundaries",
                    system_prompt=BOUNDARY_SYSTEM_PROMPT,
                    user_prompt=boundary_user_prompt(transcription),
                    temperature=0.0,
                ),
            )
            usage = _response_usage(response)
            parsed, error = parse_typed_json_lenient(_BoundaryPayload, response.content)
            if parsed is None:
                warning = ScenePipelineWarning(
                    code="scene_boundaries_invalid_json",
                    message=f"Boundary JSON parse failed: {error}",
                )
                return BoundaryResult([], [warning], usage)
            return BoundaryResult(
                _normalize_boundaries(parsed.scenes, transcription),
                [],
                usage,
            )
        except LLMRunnerError as exc:
            LOGGER.warning("Scene boundary full pass failed; trying blocks: %s", exc)
            return self._identify_boundaries_hierarchical(transcription, str(exc))

    def _identify_boundaries_hierarchical(
        self,
        transcription: MergedTranscription,
        initial_error: str,
    ) -> BoundaryResult:
        """Run block-level boundary detection and merge the results."""
        warnings = [
            ScenePipelineWarning(
                code="scene_boundaries_full_pass_failed",
                message=initial_error,
            )
        ]
        usage = LLMUsageDelta()
        block_results: list[list[SceneBoundary]] = []
        for block_index, block in enumerate(
            _split_transcription_blocks(transcription, self._block_seconds),
        ):
            try:
                response = self._llm_runner.run(
                    LLMRequest(
                        purpose="analysis.scenes.boundaries.block",
                        system_prompt=BOUNDARY_SYSTEM_PROMPT,
                        user_prompt=boundary_user_prompt(block),
                        temperature=0.0,
                        metadata={"block_index": block_index},
                    ),
                )
            except LLMRunnerError as exc:
                warnings.append(
                    ScenePipelineWarning(
                        code="scene_boundaries_block_failed",
                        message=str(exc),
                        metadata={"block_index": block_index},
                    )
                )
                continue
            usage = _merge_usage(usage, _response_usage(response))
            parsed, error = parse_typed_json_lenient(_BoundaryPayload, response.content)
            if parsed is None:
                warnings.append(
                    ScenePipelineWarning(
                        code="scene_boundaries_block_invalid_json",
                        message=f"Block boundary JSON parse failed: {error}",
                        metadata={"block_index": block_index},
                    )
                )
                continue
            block_results.append(parsed.scenes)

        if not block_results:
            return BoundaryResult([], warnings, usage)

        try:
            response = self._llm_runner.run(
                LLMRequest(
                    purpose="analysis.scenes.boundaries.merge",
                    system_prompt=BOUNDARY_SYSTEM_PROMPT,
                    user_prompt=boundary_merge_user_prompt(block_results),
                    temperature=0.0,
                ),
            )
        except LLMRunnerError as exc:
            warnings.append(
                ScenePipelineWarning(
                    code="scene_boundaries_merge_failed",
                    message=str(exc),
                )
            )
            merged = [scene for block in block_results for scene in block]
            return BoundaryResult(
                _normalize_boundaries(merged, transcription),
                warnings,
                usage,
            )
        usage = _merge_usage(usage, _response_usage(response))
        parsed, error = parse_typed_json_lenient(_BoundaryPayload, response.content)
        if parsed is None:
            warnings.append(
                ScenePipelineWarning(
                    code="scene_boundaries_merge_invalid_json",
                    message=f"Boundary merge JSON parse failed: {error}",
                )
            )
            merged = [scene for block in block_results for scene in block]
        else:
            merged = parsed.scenes
        return BoundaryResult(
            _normalize_boundaries(merged, transcription),
            warnings,
            usage,
        )


def _normalize_boundaries(
    scenes: list[SceneBoundary],
    transcription: MergedTranscription,
) -> list[SceneBoundary]:
    """Sort, clamp, and renumber boundary rows without being overly strict."""
    if not scenes:
        return []
    duration = _duration(transcription)
    normalized: list[SceneBoundary] = []
    for index, scene in enumerate(sorted(scenes, key=lambda item: item.start), start=1):
        start = max(0.0, min(scene.start, duration))
        end = max(start, min(scene.end, duration))
        normalized.append(
            scene.model_copy(
                update={
                    "scene_id": index,
                    "start": start,
                    "end": end,
                    "title": scene.title.strip() or f"Scene {index}",
                }
            )
        )
    return [scene for scene in normalized if scene.end > scene.start]


def _split_transcription_blocks(
    transcription: MergedTranscription,
    block_seconds: float,
) -> list[MergedTranscription]:
    """Split a transcription into broad temporal blocks for fallback analysis."""
    if not transcription.segments:
        return []
    if block_seconds <= 0:
        return [transcription]
    blocks: list[MergedTranscription] = []
    current: list[TranscriptionSegment] = []
    block_start = transcription.segments[0].start
    for segment in transcription.segments:
        if current and segment.start >= block_start + block_seconds:
            blocks.append(
                MergedTranscription(
                    text=_render_block_text(current),
                    segments=list(current),
                    language=transcription.language,
                    duration=transcription.duration,
                    model=transcription.model,
                )
            )
            current = []
            block_start = segment.start
        current.append(segment)
    if current:
        blocks.append(
            MergedTranscription(
                text=_render_block_text(current),
                segments=list(current),
                language=transcription.language,
                duration=transcription.duration,
                model=transcription.model,
            )
        )
    return blocks


def _render_block_text(segments: list[TranscriptionSegment]) -> str:
    """Render a block transcript with one speaker-labelled line per segment."""
    lines: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        speaker = segment.author.speaker if segment.author else "unknown"
        lines.append(f"[{speaker}] {' '.join(text.split())}")
    return "\n".join(lines)


def _duration(transcription: MergedTranscription) -> float:
    """Return transcription duration from metadata or segment bounds."""
    if transcription.duration is not None:
        return transcription.duration
    if not transcription.segments:
        return 0.0
    return max(segment.end for segment in transcription.segments)
