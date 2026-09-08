from __future__ import annotations

import json
from pathlib import Path

import pytest

from tara.analysis.llm_runner import (
    LLMBackendError,
    LLMRequest,
    LLMResponse,
    LLMRunner,
    LLMRunnerConfig,
)
from tara.cli import TaraArgs
from tara.config import TaraConfig
from tara.pipeline import TaraControlAgent, TaraPipelineCancelled, _run_scene_pipeline
from tara.schemas.merged_transcription import (
    TranscriptionSegment,
    new_merged_transcription,
)
from tara.web_contracts import EventType, StageCode, WarningCode


class _EventSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


class _Backend:
    backend_name = "api"

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    def run(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]


def _request() -> LLMRequest:
    return LLMRequest(purpose="test", system_prompt="safe", user_prompt="safe")


def test_transcription_callbacks_emit_bounded_ordered_web_progress() -> None:
    sink = _EventSink()
    agent = TaraControlAgent(
        TaraArgs(skip_analysis=True),
        config=TaraConfig(),
        event_sink=sink,
    )

    for index, total, ratio in (
        (2, 2, 0.5),
        (1, 2, 0.25),
        (2, 2, 1.0),
        (1, 2, 0.75),
        (1, 2, 0.5),
        (1, 2, 2.0),
    ):
        agent._transcription_progress(index, total, ratio)

    events = sink.events
    assert all(event.event_type is EventType.STAGE_PROGRESS for event in events)
    assert all(event.stage_code is StageCode.TRANSCRIPTION for event in events)
    ratios = [event.current_ratio for event in events]
    assert ratios == [0.25, 0.375, 0.625, 0.875, 1.0]
    assert ratios == sorted(ratios)
    assert all(0.0 <= ratio <= 1.0 for ratio in ratios)


def test_llm_retry_callback_is_bounded_and_hides_backend_message() -> None:
    backend = _Backend(
        [LLMBackendError("provider secret /path"), LLMResponse("ok", "m", "api")]
    )
    retries: list[int] = []
    sink = _EventSink()
    agent = TaraControlAgent(
        TaraArgs(skip_analysis=True),
        config=TaraConfig(),
        event_sink=sink,
    )

    def retry(attempt: int) -> None:
        retries.append(attempt)
        agent._retry_scheduled(attempt)

    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="m", max_retries=1),
        api_backend=backend,
        sleep=lambda _: None,
        retry_callback=retry,
    )
    assert runner.run(_request()).content == "ok"
    assert retries == [2]
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_type is EventType.RETRY_SCHEDULED
    assert event.stage_code is StageCode.NARRATIVE_ANALYSIS
    assert event.code is WarningCode.RETRY_IN_PROGRESS
    assert event.parameters == {"attempt": 2}
    public = json.dumps(event.to_payload())
    assert "provider secret" not in public
    assert "/path" not in public


@pytest.mark.parametrize("after", [False, True])
def test_llm_cancellation_stops_before_or_after_backend(after: bool) -> None:
    backend = _Backend([LLMResponse("ok", "m", "api")])
    checks = 0

    def cancel() -> None:
        nonlocal checks
        checks += 1
        if (not after and checks == 1) or (after and checks == 2):
            raise TaraPipelineCancelled("cancelled")

    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="m", max_retries=2),
        api_backend=backend,
        cancellation_check=cancel,
    )
    with pytest.raises(TaraPipelineCancelled):
        runner.run(_request())
    assert backend.calls == (1 if after else 0)


def test_scene_fallback_warns_but_never_absorbs_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcription = new_merged_transcription(
        text="x", segments=[TranscriptionSegment(start=0, end=1, text="x")]
    )
    sink = _EventSink()
    agent = TaraControlAgent(
        TaraArgs(skip_analysis=True),
        config=TaraConfig(),
        event_sink=sink,
    )

    def ordinary(*args: object, **kwargs: object) -> object:
        raise RuntimeError("provider secret")

    monkeypatch.setattr("tara.pipeline.SceneAnalysisPipeline.run", ordinary)
    assert _run_scene_pipeline(
        transcription=transcription,
        merged_transcription_path=tmp_path / "merged.yaml",
        config=TaraConfig(),
        llm_runner=None,
        warning_callback=agent._scene_fallback_warning,
    ).timeline
    assert len(sink.events) == 1
    warning = sink.events[0]
    assert warning.event_type is EventType.WARNING_RAISED
    assert warning.stage_code is StageCode.NARRATIVE_ANALYSIS
    assert warning.code is WarningCode.SERVICE_DEGRADED
    assert warning.parameters == {"component": "scene_pipeline"}
    assert "provider secret" not in json.dumps(warning.to_payload())

    def cancelled(*args: object, **kwargs: object) -> object:
        raise TaraPipelineCancelled("cancelled")

    monkeypatch.setattr("tara.pipeline.SceneAnalysisPipeline.run", cancelled)
    with pytest.raises(TaraPipelineCancelled):
        _run_scene_pipeline(
            transcription=transcription,
            merged_transcription_path=tmp_path / "merged.yaml",
            config=TaraConfig(),
            llm_runner=None,
            warning_callback=agent._scene_fallback_warning,
        )
    assert len(sink.events) == 1
