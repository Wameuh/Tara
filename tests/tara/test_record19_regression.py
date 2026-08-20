"""Regression checks for Record19-style Cursor CLI probe vs analysis accounting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tara.analysis import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.cli import parse_args
from tara.config import TaraConfig, load_config
from tara.pipeline import (
    TaraControlAgent,
    TaraPipelineError,
    _acceptance_backend_for_quality,
)
from tara.yaml_utils import load_yaml_or_json

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "record19_merged_transcription.yaml"
)


def test_api_backend_auto_requires_default_api_model(tmp_path: Path) -> None:
    """API backend with model Auto must resolve default_api_model or fail fast."""
    merged = tmp_path / "merged_transcription.yaml"
    merged.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "llm": {
                        "backend": "api",
                        "model": "Auto",
                        "cursor_cli_probe": False,
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    args = parse_args(
        ["--merged-transcription", str(merged), "--config", str(config_path)],
    )
    with pytest.raises(TaraPipelineError, match="default_api_model"):
        TaraControlAgent(args).run()


def test_cursor_cli_probe_run_exposes_probe_counter_separate_from_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe usage must appear under probe_llm_call_count, not as analysis-only."""
    merged = tmp_path / "merged_transcription.yaml"
    merged.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "llm": {
                        "backend": "cursor_cli",
                        "model": "Auto",
                        "cursor_cli_probe": True,
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    class StubCursorBackend:
        backend_name: str = "cursor_cli"

        def run(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                content="OK",
                model="Auto",
                backend="cursor_cli",
                total_tokens=5,
            )

    def fake_build(config: TaraConfig, usage_report=None) -> LLMRunner:
        return LLMRunner(
            LLMRunnerConfig(backend="cursor_cli", model=None),
            cursor_backend=StubCursorBackend(),
        )

    monkeypatch.setattr("tara.pipeline._build_llm_runner", fake_build)
    args = parse_args(
        ["--merged-transcription", str(merged), "--config", str(config_path)],
    )
    result = TaraControlAgent(args).run()
    payload = load_yaml_or_json(result.session_summary_json_path)
    assert payload["usage"]["probe_llm_call_count"] >= 1
    assert payload["usage"]["analysis_llm_call_count"] >= 1
    assert (
        payload["usage"]["probe_llm_call_count"]
        != payload["usage"]["analysis_llm_call_count"]
    )


def test_acceptance_backend_downgrades_cursor_cli_without_probe(tmp_path: Path) -> None:
    """Quality gates use deterministic backend when cursor_cli has no probe."""
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "llm": {
                        "backend": "cursor_cli",
                        "cursor_cli_probe": False,
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert _acceptance_backend_for_quality(cfg) == "deterministic"
