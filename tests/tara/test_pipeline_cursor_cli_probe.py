"""Tests for optional Cursor CLI pipeline probe before deterministic analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tara.analysis import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.cli import parse_args
from tara.config import TaraConfig
from tara.pipeline import TaraControlAgent


def test_prior_context_reaches_cursor_cli_probe_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prior-session markdown is embedded in the probe user prompt."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    prior = tmp_path / "Resume.md"
    prior.write_text("## Earlier\nThe temple was lost.", encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "llm": {
                        "backend": "cursor_cli",
                        "cursor_cli_probe": True,
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    seen: list[str] = []

    class CapturingBackend:
        backend_name: str = "cursor_cli"

        def run(self, request: LLMRequest) -> LLMResponse:
            seen.append(request.user_prompt)
            return LLMResponse(
                content="OK",
                model="Auto",
                backend="cursor_cli",
                total_tokens=2,
            )

    monkeypatch.setattr(
        "tara.pipeline._build_llm_runner",
        lambda c: LLMRunner(
            LLMRunnerConfig(backend="cursor_cli", model=None),
            cursor_backend=CapturingBackend(),
        ),
    )
    args = parse_args(
        [
            "--merged-transcription",
            str(merged),
            "--config",
            str(config_path),
            "--prior-context",
            str(prior),
        ],
    )
    TaraControlAgent(args).run()

    assert seen and "--- prior sessions ---" in seen[0]
    assert "The temple was lost." in seen[0]


def test_cursor_cli_probe_merges_usage_into_session_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the probe runs, one LLM call is counted in session_summary.json."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
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
        """Fake Cursor CLI backend returning fixed usage."""

        backend_name: str = "cursor_cli"

        def run(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                content="OK",
                model="Auto",
                backend="cursor_cli",
                input_tokens=50,
                output_tokens=10,
                total_tokens=60,
                estimated_cost_usd=0.001,
            )

    def fake_build(config: TaraConfig) -> LLMRunner:
        return LLMRunner(
            LLMRunnerConfig(backend="cursor_cli", model=None),
            cursor_backend=StubCursorBackend(),
        )

    monkeypatch.setattr("tara.pipeline._build_llm_runner", fake_build)

    args = parse_args(
        ["--merged-transcription", str(merged), "--config", str(config_path)],
    )
    result = TaraControlAgent(args).run()

    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["usage"]["llm_call_count"] == 1
    assert payload["usage"]["estimated_llm_tokens"] == 60
    assert payload["usage"]["backend"] == "cursor_cli"
    assert payload["summary"]["llm_call_count"] == 1
    assert payload["summary"]["estimated_llm_tokens"] == 60
    assert payload["acceptance"]["llm_call_count"] == 1
    assert payload["acceptance"]["estimated_llm_tokens"] == 60
    assert (
        payload["usage"]["estimated_llm_tokens"]
        == payload["acceptance"]["estimated_llm_tokens"]
    )


def test_cursor_cli_without_probe_skips_llm_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With backend cursor_cli but probe off, the runner must not be invoked."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "llm": {
                        "backend": "cursor_cli",
                        "model": "Auto",
                        "cursor_cli_probe": False,
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    class ExplodingBackend:
        """Fail fast if Cursor CLI is invoked."""

        backend_name: str = "cursor_cli"

        def run(self, request: LLMRequest) -> LLMResponse:
            raise AssertionError("Cursor CLI must not run when probe is disabled")

    def fake_build(config: TaraConfig) -> LLMRunner:
        return LLMRunner(
            LLMRunnerConfig(backend="cursor_cli", model=None),
            cursor_backend=ExplodingBackend(),
        )

    monkeypatch.setattr("tara.pipeline._build_llm_runner", fake_build)

    args = parse_args(
        ["--merged-transcription", str(merged), "--config", str(config_path)],
    )
    TaraControlAgent(args).run()


def test_cli_cursor_cli_probe_flag_enables_probe_without_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI `--cursor-cli-probe` enables the probe with a cursor_cli backend override."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    cfg = {"analysis": {"llm": {"backend": "api", "cursor_cli_probe": False}}}
    config_path.write_text(json.dumps(cfg), encoding="utf-8")

    class StubCursorBackend:
        backend_name: str = "cursor_cli"

        def run(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                content="OK",
                model="Auto",
                backend="cursor_cli",
                total_tokens=3,
            )

    monkeypatch.setattr(
        "tara.pipeline._build_llm_runner",
        lambda c: LLMRunner(
            LLMRunnerConfig(backend="cursor_cli", model=None),
            cursor_backend=StubCursorBackend(),
        ),
    )
    args = parse_args(
        [
            "--merged-transcription",
            str(merged),
            "--config",
            str(config_path),
            "--analysis-backend",
            "cursor_cli",
            "--cursor-cli-probe",
        ],
    )
    result = TaraControlAgent(args).run()
    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["usage"]["llm_call_count"] == 1


def test_env_tara_cursor_cli_probe_enables_probe_without_json_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TARA_CURSOR_CLI_PROBE=1 enables the probe when JSON leaves it false."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
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

    class StubCursorBackend:
        backend_name: str = "cursor_cli"

        def run(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                content="OK",
                model="Auto",
                backend="cursor_cli",
                total_tokens=4,
                estimated_cost_usd=None,
            )

    monkeypatch.setenv("TARA_CURSOR_CLI_PROBE", "1")
    monkeypatch.setattr(
        "tara.pipeline._build_llm_runner",
        lambda c: LLMRunner(
            LLMRunnerConfig(backend="cursor_cli", model=None),
            cursor_backend=StubCursorBackend(),
        ),
    )

    try:
        args = parse_args(
            ["--merged-transcription", str(merged), "--config", str(config_path)],
        )
        result = TaraControlAgent(args).run()
    finally:
        monkeypatch.delenv("TARA_CURSOR_CLI_PROBE", raising=False)

    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["usage"]["llm_call_count"] == 1


@pytest.mark.skipif(
    os.environ.get("TARA_CURSOR_CLI_E2E") != "1",
    reason="Set TARA_CURSOR_CLI_E2E=1 to run a real Cursor CLI pipeline probe.",
)
def test_real_cursor_cli_probe_smoke(tmp_path: Path) -> None:
    """Optional local smoke: one real Cursor CLI call (slow, needs agent on PATH)."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "llm": {
                        "backend": "cursor_cli",
                        "model": "Auto",
                        "cursor_cli_probe": True,
                        "timeout_seconds": 120,
                        "retries": 0,
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    args = parse_args(
        ["--merged-transcription", str(merged), "--config", str(config_path)],
    )
    result = TaraControlAgent(args).run()
    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["usage"]["llm_call_count"] >= 1


def _merged_payload() -> str:
    """Minimal merged transcription matching deterministic specialist queries."""
    return json.dumps(
        {
            "text": (
                "Le combat commence au temple. La lance touche l'ennemi. "
                "Une potion de soin stabilise Karknyr."
            ),
            "segments": [
                {
                    "start": 0.0,
                    "end": 30.0,
                    "text": "Le combat commence au temple.",
                },
                {
                    "start": 30.0,
                    "end": 60.0,
                    "text": "La lance touche l'ennemi.",
                },
                {
                    "start": 60.0,
                    "end": 90.0,
                    "text": "Une potion de soin stabilise Karknyr.",
                },
            ],
            "language": "fr",
            "duration": 90.0,
            "model": "fixture",
        },
    )
