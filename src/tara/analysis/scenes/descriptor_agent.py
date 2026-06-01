"""LLM-backed long scene description generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from tara.analysis.agentic_llm import (
    LLMUsageDelta,
    _merge_usage,
    _response_usage,
    parse_with_single_json_repair,
)
from tara.analysis.llm_runner import LLMRequest, LLMRunner, LLMRunnerError
from tara.analysis.models import TaraModel
from tara.analysis.scenes.cache import read_json, write_json
from tara.analysis.scenes.models import (
    SceneDescription,
    ScenePipelineWarning,
    SceneTimeline,
    SceneTranscription,
)
from tara.analysis.scenes.prompts import (
    DESCRIPTION_SYSTEM_PROMPT,
    description_user_prompt,
)

LOGGER = logging.getLogger(__name__)


class _DescriptionsPayload(TaraModel):
    """On-disk scene descriptions artifact."""

    scene_descriptions: list[SceneDescription] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


@dataclass(slots=True)
class DescriptionResult:
    """Scene description output and usage accounting."""

    timeline: SceneTimeline
    usage: LLMUsageDelta
    warnings: list[ScenePipelineWarning]


class SceneDescriptorAgent:
    """Generate and cache long descriptions for scene transcriptions."""

    def __init__(
        self,
        llm_runner: LLMRunner,
        *,
        output_path: Path,
        resume_partial: bool = True,
    ) -> None:
        """Initialize the descriptor."""
        self._llm_runner = llm_runner
        self._output_path = output_path
        self._resume_partial = resume_partial

    def describe_all(self, scenes: list[SceneTranscription]) -> DescriptionResult:
        """Describe all scenes, reusing fresh cached rows when available."""
        cached = self._load_cached_descriptions() if self._resume_partial else {}
        descriptions: list[SceneDescription] = []
        warnings: list[ScenePipelineWarning] = []
        usage = LLMUsageDelta()
        for scene in scenes:
            cached_scene = cached.get(scene.scene_id)
            if (
                cached_scene is not None
                and cached_scene.source_hash == scene.source_hash
            ):
                descriptions.append(cached_scene)
                continue
            if not scene.text.strip() or not scene.segments:
                warnings.append(
                    ScenePipelineWarning(
                        code="scene_description_empty_scene",
                        message="Scene has no transcript text; skipped.",
                        scene_id=scene.scene_id,
                    )
                )
                continue
            try:
                response = self._llm_runner.run(
                    LLMRequest(
                        purpose="analysis.scenes.describe",
                        system_prompt=DESCRIPTION_SYSTEM_PROMPT,
                        user_prompt=description_user_prompt(scene),
                        temperature=0.0,
                        metadata={"scene_id": scene.scene_id},
                    ),
                )
            except LLMRunnerError as exc:
                LOGGER.warning(
                    "Scene description failed for scene %s: %s",
                    scene.scene_id,
                    exc,
                )
                warnings.append(
                    ScenePipelineWarning(
                        code="scene_description_llm_failed",
                        message=str(exc),
                        scene_id=scene.scene_id,
                    )
                )
                continue
            scene_usage = _response_usage(response)
            parsed, scene_usage = parse_with_single_json_repair(
                SceneDescription,
                response.content,
                self._llm_runner,
                repair_purpose_prefix="analysis.scenes.describe",
                schema_description=(
                    '{"scene_id","title","start","end","summary","description",'
                    '"facts","key_actions","state_changes","continuity_impacts"}'
                ),
                initial_usage=scene_usage,
            )
            usage = _merge_usage(usage, scene_usage)
            if parsed is None:
                warnings.append(
                    ScenePipelineWarning(
                        code="scene_description_invalid_json",
                        message="Scene description JSON parse failed after repair.",
                        scene_id=scene.scene_id,
                    )
                )
                continue
            descriptions.append(
                parsed.model_copy(
                    update={
                        "scene_id": scene.scene_id,
                        "start": scene.start,
                        "end": scene.end,
                        "source_hash": scene.source_hash,
                        "metadata": {
                            **parsed.metadata,
                            "scene_file": scene.output_path or "",
                        },
                    }
                )
            )
        descriptions.sort(key=lambda item: item.scene_id)
        timeline = SceneTimeline(
            scenes=descriptions,
            warnings=warnings,
            metadata={"source": "scene_descriptor"},
        )
        self._write_output(timeline)
        return DescriptionResult(timeline=timeline, usage=usage, warnings=warnings)

    def _load_cached_descriptions(self) -> dict[int, SceneDescription]:
        """Load fresh-enough cached descriptions by scene id."""
        data = read_json(self._output_path)
        if data is None:
            return {}
        try:
            payload = _DescriptionsPayload.from_dict(data)
        except ValueError:
            return {}
        return {scene.scene_id: scene for scene in payload.scene_descriptions}

    def _write_output(self, timeline: SceneTimeline) -> None:
        """Write the scene descriptions artifact."""
        write_json(
            self._output_path,
            {
                "scene_descriptions": [
                    scene.to_dict() for scene in timeline.scenes
                ],
                "warnings": [warning.to_dict() for warning in timeline.warnings],
                "metadata": timeline.metadata,
            },
        )
