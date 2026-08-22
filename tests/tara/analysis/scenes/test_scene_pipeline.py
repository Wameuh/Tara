"""Tests for scene-driven blackboard enrichment."""

from __future__ import annotations

from pathlib import Path

from tara.analysis.agentic_llm import run_composer_llm
from tara.analysis.llm_runner import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.analysis.models import MergedTranscription, SegmentAuthor
from tara.analysis.scenes import (
    SceneAnalysisPipeline,
    SceneBlackboardIngestor,
    SceneBoundary,
    SceneSplitter,
)
from tara.config import AnalysisScenesConfig
from tara.schemas.merged_transcription import new_merged_transcription
from tara.yaml_utils import load_yaml_or_json, to_yaml


class _SceneBackend:
    """LLM backend stub for scene pipeline calls."""

    backend_name = "api"

    def __init__(self) -> None:
        """Initialize request capture."""
        self.requests: list[LLMRequest] = []

    def run(self, request: LLMRequest) -> LLMResponse:
        """Return structured payloads for scene purposes."""
        self.requests.append(request)
        if request.purpose == "analysis.scenes.boundaries":
            return LLMResponse(
                content=to_yaml(
                    {
                        "scenes": [
                            {
                                "scene_id": 1,
                                "title": "Opening fight",
                                "start": 0.0,
                                "end": 12.0,
                                "summary": "The fight resumes.",
                            }
                        ]
                    }
                ),
                model="gpt-test",
                backend="api",
                total_tokens=10,
            )
        if request.purpose == "analysis.scenes.describe":
            return LLMResponse(
                content=to_yaml(
                    {
                        "scene_id": 1,
                        "title": "Opening fight",
                        "start": 0.0,
                        "end": 12.0,
                        "summary": "The fight resumes.",
                        "description": "The party resumes a dangerous fight.",
                        "facts": [
                            {
                                "claim": "The party resumes an active fight.",
                                "claim_type": "chronology",
                                "confidence": "high",
                                "importance": 4,
                                "is_critical": True,
                                "supporting_segment_ids": [0, 1],
                            }
                        ],
                        "key_actions": ["The group resumes combat."],
                        "state_changes": ["The fight remains active."],
                        "continuity_impacts": [
                            "The next choices happen under pressure.",
                        ],
                    }
                ),
                model="gpt-test",
                backend="api",
                total_tokens=20,
            )
        raise AssertionError(f"unexpected purpose {request.purpose}")


def test_scene_splitter_preserves_overlapping_segments_and_speaker_metadata(
    tmp_path: Path,
) -> None:
    """Splitter writes per-scene files with overlapping segments and author data."""
    transcription = _transcription()
    scenes = SceneSplitter(scenes_dir=tmp_path / "scenes").split(
        transcription,
        [
            SceneBoundary(
                scene_id=1,
                title="Opening",
                start=0.0,
                end=6.0,
                summary="Start",
            )
        ],
    )

    assert len(scenes) == 1
    assert scenes[0].segments[0]["author"] == {
        "speaker": "wameuh",
        "source_file": "dm.yaml",
    }
    assert scenes[0].text.splitlines() == [
        "[wameuh] Fight resumes.",
        "[orvex] Orvex helps.",
    ]
    assert (tmp_path / "scenes" / "scene_001.yaml").exists()


def test_scene_pipeline_writes_artifacts_and_ingests_scene_facts(
    tmp_path: Path,
) -> None:
    """Pipeline produces artifacts and scene facts become EvidenceAnswer rows."""
    merged_path = tmp_path / "merged_transcription.yaml"
    merged_path.write_text(
        to_yaml(_transcription().model_dump(mode="json")), encoding="utf-8"
    )
    backend = _SceneBackend()
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=backend,
    )

    result = SceneAnalysisPipeline(AnalysisScenesConfig()).run(
        transcription=_transcription(),
        merged_transcription_path=merged_path,
        llm_runner=runner,
        context_text=(
            "Système : Fate\nWameuh facilite la partie\nWillygorn incarne Kaknyr"
        ),
    )
    answers = SceneBlackboardIngestor().to_evidence_answers(result.timeline)
    describe_request = next(
        request
        for request in backend.requests
        if request.purpose == "analysis.scenes.describe"
    )
    descriptions_payload = load_yaml_or_json(tmp_path / "scene_descriptions.yaml")

    assert (tmp_path / "scene_analysis.yaml").exists()
    assert (tmp_path / "scenes" / "scene_001.yaml").exists()
    assert (tmp_path / "scene_descriptions.yaml").exists()
    assert "Wameuh facilite la partie" in describe_request.user_prompt
    assert "Audio speaker labels identify the recording track owner" in (
        describe_request.user_prompt
    )
    assert "Do not extrapolate beyond the transcript" in describe_request.user_prompt
    assert "never present a guess as fact" in describe_request.user_prompt
    assert (
        descriptions_payload["metadata"]["prompt_version"] == "speaker_attribution_v4"
    )
    assert result.scene_llm_call_count == 2
    assert answers[0].answer_id == "chronology_scene_001_00"
    assert answers[0].metadata["source"] == "scene_description"
    assert answers[0].metadata["scene_id"] == 1


def test_composer_prompt_receives_scene_timeline(tmp_path: Path) -> None:
    """The final composer gets the scene timeline block."""
    merged_path = tmp_path / "merged_transcription.yaml"
    merged_path.write_text(
        to_yaml(_transcription().model_dump(mode="json")), encoding="utf-8"
    )
    scene_backend = _SceneBackend()
    scene_runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=scene_backend,
    )
    scene_result = SceneAnalysisPipeline(AnalysisScenesConfig()).run(
        transcription=_transcription(),
        merged_transcription_path=merged_path,
        llm_runner=scene_runner,
    )

    class _ComposerBackend:
        backend_name = "api"

        def __init__(self) -> None:
            self.prompt = ""
            self.system_prompt = ""

        def run(self, request: LLMRequest) -> LLMResponse:
            self.prompt = request.user_prompt
            self.system_prompt = request.system_prompt
            return LLMResponse(
                content=to_yaml(
                    {
                        "markdown": "# Résumé de session\n\n## Résumé express\n- X",
                        "sections": [
                            {
                                "section_id": "resume",
                                "title": "Résumé express",
                                "content": "- X",
                                "supporting_answer_ids": ["chronology_scene_001_00"],
                            }
                        ],
                    }
                ),
                model="gpt-test",
                backend="api",
            )

    composer_backend = _ComposerBackend()
    composer_runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=composer_backend,
    )
    run_composer_llm(
        composer_runner,
        facts_payload=[
            {
                "answer_id": "chronology_scene_001_00",
                "claim": "The party resumes an active fight.",
            }
        ],
        do_not_claim=[],
        scene_timeline=scene_result.timeline,
    )

    assert "Scene timeline" in composer_backend.prompt
    assert "Opening fight" in composer_backend.prompt
    assert "The party resumes an active fight." in composer_backend.prompt
    assert "concise but not skeletal" in composer_backend.prompt
    assert "usually two compact sentences per group" in composer_backend.prompt
    assert "sentence one names the major event/outcome" in composer_backend.prompt
    assert (
        "Keep low-level mechanics out of 'R\u00e9sum\u00e9 express'"
        in composer_backend.prompt
    )
    assert "temporary protection amounts" in composer_backend.prompt
    assert "Summarize their narrative effect instead" in composer_backend.prompt
    assert "at most one short orientation sentence" in composer_backend.prompt
    assert "Do not print answer IDs" in composer_backend.prompt
    assert "Do not extrapolate beyond the transcript" in composer_backend.system_prompt
    assert "never present a guess as fact" in composer_backend.system_prompt
    assert "Conflict summarization policy (critical)" in composer_backend.system_prompt
    assert "surmonte les gardiens" in composer_backend.system_prompt
    assert "surmonte les gardiens" in composer_backend.system_prompt


def _transcription() -> MergedTranscription:
    """Build a small transcription fixture."""
    return new_merged_transcription(
        text="Fight resumes. Orvex helps.",
        segments=[
            {
                "start": 0.0,
                "end": 5.0,
                "text": "Fight resumes.",
                "author": SegmentAuthor(speaker="wameuh", source_file="dm.yaml"),
            },
            {
                "start": 5.0,
                "end": 12.0,
                "text": "Orvex helps.",
                "author": SegmentAuthor(speaker="orvex", source_file="npc.yaml"),
            },
        ],
        language="en",
        duration=12.0,
    )
