"""LLM-backed long scene description generation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    description_user_prompt_with_context,
)

LOGGER = logging.getLogger(__name__)
SCENE_DESCRIPTION_PROMPT_VERSION = "speaker_attribution_v4"
SceneDescriptionResult = tuple[
    SceneDescription | None,
    LLMUsageDelta,
    ScenePipelineWarning | None,
]


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
        parallel: bool = False,
    ) -> None:
        """Initialize the descriptor."""
        self._llm_runner = llm_runner
        self._output_path = output_path
        self._resume_partial = resume_partial
        self._parallel = parallel

    def describe_all(
        self,
        scenes: list[SceneTranscription],
        *,
        context_text: str | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> DescriptionResult:
        """Describe all scenes, reusing fresh cached rows when available."""
        cached = self._load_cached_descriptions() if self._resume_partial else {}
        descriptions: list[SceneDescription] = []
        warnings: list[ScenePipelineWarning] = []
        usage = LLMUsageDelta()
        pending: list[SceneTranscription] = []
        total_scenes = max(1, len(scenes))
        completed_scenes = 0
        if progress_callback is not None:
            progress_callback("scene_descriptions", 0, total_scenes)
        for scene in scenes:
            cached_scene = cached.get(scene.scene_id)
            if (
                cached_scene is not None
                and cached_scene.source_hash == scene.source_hash
            ):
                descriptions.append(cached_scene)
                completed_scenes += 1
                continue
            if not scene.text.strip() or not scene.segments:
                warnings.append(
                    ScenePipelineWarning(
                        code="scene_description_empty_scene",
                        message="Scene has no transcript text; skipped.",
                        scene_id=scene.scene_id,
                    )
                )
                completed_scenes += 1
                continue
            pending.append(scene)
        if progress_callback is not None and completed_scenes:
            progress_callback(
                "scene_descriptions",
                completed_scenes,
                total_scenes,
            )

        if self._parallel and len(pending) > 1:
            results_by_id: dict[int, SceneDescriptionResult] = {}
            with ThreadPoolExecutor(max_workers=len(pending)) as executor:
                futures = {
                    executor.submit(
                        self._describe_scene,
                        scene,
                        context_text,
                    ): scene.scene_id
                    for scene in pending
                }
                for future in as_completed(futures):
                    scene_id = futures[future]
                    results_by_id[scene_id] = future.result()
                    completed_scenes += 1
                    if progress_callback is not None:
                        progress_callback(
                            "scene_descriptions",
                            completed_scenes,
                            total_scenes,
                        )
            for scene in pending:
                described, scene_usage, warning = results_by_id[scene.scene_id]
                usage = _merge_usage(usage, scene_usage)
                if warning is not None:
                    warnings.append(warning)
                if described is not None:
                    descriptions.append(described)
        else:
            for scene in pending:
                described, scene_usage, warning = self._describe_scene(
                    scene,
                    context_text,
                )
                completed_scenes += 1
                if progress_callback is not None:
                    progress_callback(
                        "scene_descriptions",
                        completed_scenes,
                        total_scenes,
                    )
                usage = _merge_usage(usage, scene_usage)
                if warning is not None:
                    warnings.append(warning)
                if described is not None:
                    descriptions.append(described)
        descriptions.sort(key=lambda item: item.scene_id)
        timeline = SceneTimeline(
            scenes=descriptions,
            warnings=warnings,
            metadata={"source": "scene_descriptor"},
        )
        self._write_output(timeline)
        if progress_callback is not None:
            progress_callback("scene_descriptions", total_scenes, total_scenes)
        return DescriptionResult(timeline=timeline, usage=usage, warnings=warnings)

    def _describe_scene(
        self,
        scene: SceneTranscription,
        context_text: str | None,
    ) -> SceneDescriptionResult:
        """Describe one scene and return parsed output or a warning."""
        try:
            response = self._llm_runner.run(
                LLMRequest(
                    purpose="analysis.scenes.describe",
                    stage="scenes.description",
                    system_prompt=DESCRIPTION_SYSTEM_PROMPT,
                    user_prompt=description_user_prompt_with_context(
                        scene,
                        context_text,
                    ),
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
            return (
                None,
                LLMUsageDelta(),
                ScenePipelineWarning(
                    code="scene_description_llm_failed",
                    message=str(exc),
                    scene_id=scene.scene_id,
                ),
            )
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
        if parsed is None:
            return (
                None,
                scene_usage,
                ScenePipelineWarning(
                    code="scene_description_invalid_json",
                    message="Scene description JSON parse failed after repair.",
                    scene_id=scene.scene_id,
                ),
            )
        return (
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
            ),
            scene_usage,
            None,
        )

    def _load_cached_descriptions(self) -> dict[int, SceneDescription]:
        """Load fresh-enough cached descriptions by scene id."""
        data = read_json(self._output_path)
        if data is None:
            return {}
        try:
            payload = _DescriptionsPayload.from_dict(data)
        except ValueError:
            return {}
        if payload.metadata.get("prompt_version") != SCENE_DESCRIPTION_PROMPT_VERSION:
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
                "metadata": {
                    **timeline.metadata,
                    "prompt_version": SCENE_DESCRIPTION_PROMPT_VERSION,
                },
            },
        )
