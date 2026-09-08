"""High-level scene analysis pipeline orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tara.analysis.agentic_llm import _merge_usage
from tara.analysis.llm_runner import LLMRunner
from tara.analysis.models import MergedTranscription
from tara.analysis.scenes.boundary_agent import SceneBoundaryAgent
from tara.analysis.scenes.cache import file_sha256, read_json, write_json
from tara.analysis.scenes.descriptor_agent import SceneDescriptorAgent
from tara.analysis.scenes.models import (
    SceneBoundary,
    ScenePipelineResult,
    ScenePipelineWarning,
    SceneTimeline,
)
from tara.analysis.scenes.splitter import SceneSplitter

LOGGER = logging.getLogger(__name__)


class SceneAnalysisPipeline:
    """Orchestrate boundaries, splitting, descriptions, and scene timeline."""

    def __init__(self, config: Any) -> None:
        """Initialize with an AnalysisScenesConfig-like object."""
        self._config = config

    def run(
        self,
        *,
        transcription: MergedTranscription,
        merged_transcription_path: Path,
        llm_runner: LLMRunner | None,
        context_text: str | None = None,
        parallel: bool = False,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> ScenePipelineResult:
        """Run the scene pipeline or return an empty timeline on fallback."""
        if not getattr(self._config, "enabled", True):
            if progress_callback is not None:
                progress_callback("scene_boundaries", 1, 1)
                progress_callback("scene_descriptions", 1, 1)
            return ScenePipelineResult(timeline=SceneTimeline())
        if llm_runner is None:
            warning = ScenePipelineWarning(
                code="scene_pipeline_no_llm_runner",
                message="Scene pipeline requires an LLM runner; falling back.",
            )
            LOGGER.info(warning.message)
            if progress_callback is not None:
                progress_callback("scene_boundaries", 1, 1)
                progress_callback("scene_descriptions", 1, 1)
            return ScenePipelineResult(
                timeline=SceneTimeline(warnings=[warning]),
                warnings=[warning],
            )

        output_dir = merged_transcription_path.parent
        scene_analysis_path = output_dir / self._config.boundaries_filename
        scenes_dir = output_dir / self._config.scenes_dir
        scene_descriptions_path = output_dir / self._config.descriptions_filename
        source_hash = file_sha256(merged_transcription_path)
        warnings: list[ScenePipelineWarning] = []

        if progress_callback is not None:
            progress_callback("scene_boundaries", 0, 1)
        boundaries = self._load_cached_boundaries(scene_analysis_path, source_hash)
        boundary_usage = None
        if boundaries is None:
            boundary_result = SceneBoundaryAgent(
                llm_runner,
                block_seconds=self._config.boundary_block_seconds,
            ).identify_boundaries(transcription)
            boundaries = boundary_result.boundaries
            warnings.extend(boundary_result.warnings)
            boundary_usage = boundary_result.usage
            if boundaries:
                self._write_boundaries(scene_analysis_path, boundaries, source_hash)
        if progress_callback is not None:
            progress_callback("scene_boundaries", 1, 1)
        if not boundaries:
            warning = ScenePipelineWarning(
                code="scene_pipeline_no_boundaries",
                message="No usable scene boundaries were produced.",
            )
            warnings.append(warning)
            if progress_callback is not None:
                progress_callback("scene_descriptions", 1, 1)
            timeline = SceneTimeline(warnings=warnings)
            return ScenePipelineResult(
                timeline=timeline,
                scene_analysis_path=str(scene_analysis_path),
                scene_descriptions_path=str(scene_descriptions_path),
                scenes_dir=str(scenes_dir),
                warnings=warnings,
                scene_llm_call_count=(boundary_usage.calls if boundary_usage else 0),
                estimated_scene_llm_tokens=(
                    boundary_usage.tokens if boundary_usage else 0
                ),
                estimated_scene_cost_usd=(
                    boundary_usage.cost_usd
                    if boundary_usage and boundary_usage.cost_usd > 0
                    else None
                ),
            )

        scene_transcriptions = SceneSplitter(
            scenes_dir=scenes_dir,
            output_prefix=self._config.scene_file_prefix,
        ).split(transcription, boundaries)
        description_result = SceneDescriptorAgent(
            llm_runner,
            output_path=scene_descriptions_path,
            resume_partial=self._config.resume_partial_descriptions,
            parallel=parallel,
        ).describe_all(
            scene_transcriptions,
            context_text=context_text,
            progress_callback=progress_callback,
        )
        warnings.extend(description_result.warnings)
        usage = description_result.usage
        if boundary_usage is not None:
            usage = _merge_usage(boundary_usage, usage)
        timeline = description_result.timeline.model_copy(update={"warnings": warnings})
        return ScenePipelineResult(
            timeline=timeline,
            scene_analysis_path=str(scene_analysis_path),
            scene_descriptions_path=str(scene_descriptions_path),
            scenes_dir=str(scenes_dir),
            warnings=warnings,
            scene_llm_call_count=usage.calls,
            estimated_scene_llm_tokens=usage.tokens,
            estimated_scene_cost_usd=usage.cost_usd if usage.cost_usd > 0 else None,
        )

    @staticmethod
    def _load_cached_boundaries(
        path: Path,
        source_hash: str,
    ) -> list[SceneBoundary] | None:
        """Load cached boundaries when source hash matches."""
        data = read_json(path)
        if data is None:
            return None
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict) or metadata.get("source_hash") != source_hash:
            return None
        scenes = data.get("scenes", [])
        if not isinstance(scenes, list):
            return None
        try:
            return [SceneBoundary.from_dict(scene) for scene in scenes]
        except ValueError:
            return None

    @staticmethod
    def _write_boundaries(
        path: Path,
        boundaries: list[SceneBoundary],
        source_hash: str,
    ) -> None:
        """Write the scene boundary artifact."""
        write_json(
            path,
            {
                "scenes": [boundary.to_dict() for boundary in boundaries],
                "metadata": {
                    "source_hash": source_hash,
                    "total_scenes": len(boundaries),
                },
            },
        )
