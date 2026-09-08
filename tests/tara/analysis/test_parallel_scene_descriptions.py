"""Tests for parallel scene description execution."""

from __future__ import annotations

from pathlib import Path

from tara.analysis.llm_runner import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.analysis.scenes.descriptor_agent import SceneDescriptorAgent
from tara.analysis.scenes.models import SceneTranscription


class _SceneBackend:
    """Fake backend returning one valid scene description."""

    backend_name = "api"

    def run(self, request: LLMRequest) -> LLMResponse:
        """Return a minimal valid scene description payload."""
        scene_id = request.metadata.get("scene_id", 1)
        return LLMResponse(
            content=(
                f"scene_id: {scene_id}\n"
                "title: Test\n"
                "start: 0.0\n"
                "end: 10.0\n"
                "summary: Test\n"
                "description: Test\n"
                "facts: []\n"
                "key_actions: []\n"
                "state_changes: []\n"
                "continuity_impacts: []"
            ),
            model="fake",
            backend="api",
        )


def _scene(scene_id: int) -> SceneTranscription:
    """Build one scene transcription fixture."""
    return SceneTranscription(
        scene_id=scene_id,
        title=f"Scene {scene_id}",
        start=0.0,
        end=10.0,
        summary="Test",
        text="Some transcript text.",
        segments=[{"start": 0.0, "end": 10.0, "text": "Some transcript text."}],
        source_hash=f"hash-{scene_id}",
    )


def test_parallel_scene_descriptions_preserve_scene_count(tmp_path: Path) -> None:
    """Parallel scene description should keep one description per scene."""
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="fake"),
        api_backend=_SceneBackend(),
    )
    scenes = [_scene(1), _scene(2)]
    sequential = SceneDescriptorAgent(
        runner,
        output_path=tmp_path / "sequential.yaml",
        resume_partial=False,
        parallel=False,
    ).describe_all(scenes)
    progress: list[tuple[str, int, int]] = []
    parallel = SceneDescriptorAgent(
        runner,
        output_path=tmp_path / "parallel.yaml",
        resume_partial=False,
        parallel=True,
    ).describe_all(
        scenes,
        progress_callback=lambda *values: progress.append(values),
    )
    assert len(sequential.timeline.scenes) == 2
    assert len(parallel.timeline.scenes) == 2
    assert {scene.scene_id for scene in parallel.timeline.scenes} == {1, 2}
    assert progress[0] == ("scene_descriptions", 0, 2)
    assert progress[-1] == ("scene_descriptions", 2, 2)
    assert ("scene_descriptions", 1, 2) in progress
