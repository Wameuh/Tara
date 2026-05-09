"""Tests for standalone Tara CLI, configuration, and orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from tara.cli import ArgumentParserError, parse_args
from tara.config import load_config
from tara.pipeline import TaraControlAgent, TaraPipelineError
from tara.server import app
from tara.transcription import process_transcriptions


def test_parse_args_accepts_merged_transcription(tmp_path: Path) -> None:
    """The CLI accepts an existing merged transcription as the canonical input."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")

    args = parse_args([
        "--merged-transcription",
        str(merged),
        "--analysis-backend",
        "api",
    ])

    assert args.merged_transcription == merged.resolve()
    assert args.analysis_backend == "api"


def test_parse_args_rejects_missing_input() -> None:
    """The CLI requires exactly one canonical input path."""
    with pytest.raises(ArgumentParserError):
        parse_args([])


def test_load_config_reads_analysis_section(tmp_path: Path) -> None:
    """JSON config exposes the blackboard analysis section."""
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "pipeline": "blackboard_v1",
                    "llm": {"backend": "cursor_cli", "model": "Auto"},
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.analysis.pipeline == "blackboard_v1"
    assert config.analysis.llm.backend == "cursor_cli"
    assert config.analysis.llm.model == "Auto"


def test_process_transcriptions_writes_merged_input(tmp_path: Path) -> None:
    """Processing keeps transcription compatibility and writes merged JSON."""
    output_dir = tmp_path / "transcriptions"
    output_dir.mkdir()
    (output_dir / "a.json").write_text(_raw_transcription_payload(), encoding="utf-8")

    merged_path = process_transcriptions(output_dir, load_config(None))

    assert merged_path == output_dir / "merged_transcription.json"
    payload = json.loads(merged_path.read_text(encoding="utf-8"))
    assert payload["segments"][0]["text"] == "Le combat commence au temple."


def test_control_agent_runs_analysis_from_merged_transcription(tmp_path: Path) -> None:
    """A merged transcription run produces final markdown and JSON summaries."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    args = parse_args(["--merged-transcription", str(merged)])

    result = TaraControlAgent(args).run()

    assert result.session_summary_markdown_path is not None
    assert result.session_summary_markdown_path.exists()
    assert result.session_summary_json_path is not None
    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["usage"]["llm_call_count"] == 0
    assert "Résumé exécutif" in result.session_summary_markdown_path.read_text(
        encoding="utf-8",
    )


def test_control_agent_runs_from_audio_dir_with_mocked_transcription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audio path runs transcription, processing, and analysis in order."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "session.wav").write_bytes(b"fixture")

    def fake_transcribe_audio_directory(
        audio_path: Path,
        config: object,
    ) -> list[object]:
        output_dir = audio_path / "transcriptions"
        output_dir.mkdir()
        (output_dir / "session.json").write_text(
            _raw_transcription_payload(),
            encoding="utf-8",
        )
        return []

    monkeypatch.setattr(
        "tara.pipeline.transcribe_audio_directory",
        fake_transcribe_audio_directory,
    )

    result = TaraControlAgent(parse_args(["--audio-dir", str(audio_dir)])).run()

    assert result.merged_transcription_path == (
        audio_dir / "transcriptions" / "merged_transcription.json"
    )
    assert result.session_summary_json_path is not None
    assert result.session_summary_json_path.exists()


def test_control_agent_start_from_evidence_index_requires_merged_file(
    tmp_path: Path,
) -> None:
    """Resume from evidence-index fails early when merged JSON is absent."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    with pytest.raises(TaraPipelineError, match="merged transcription does not exist"):
        TaraControlAgent(
            parse_args([
                "--audio-dir",
                str(audio_dir),
                "--start-from",
                "evidence-index",
            ]),
        ).run()


def test_server_analysis_endpoint_runs_from_merged_transcription(
    tmp_path: Path,
) -> None:
    """The FastAPI analysis endpoint orchestrates from merged transcription."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")

    response = TestClient(app).post(
        "/v1/analysis",
        json={"merged_transcription": str(merged)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_summary_json_path"].endswith("session_summary.json")


def test_server_health_endpoint() -> None:
    """The server exposes a health endpoint."""
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_server_runs_endpoint_validates_missing_path() -> None:
    """The server maps predictable bad input to a client error."""
    response = TestClient(app).post(
        "/v1/runs",
        json={"merged_transcription": "does-not-exist.json"},
    )

    assert response.status_code == 400
    assert "File does not exist" in response.json()["detail"]


def test_server_analysis_endpoint_maps_invalid_merged_json(
    tmp_path: Path,
) -> None:
    """Invalid merged transcription content is a client error, not a 500."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text("{}", encoding="utf-8")

    response = TestClient(app).post(
        "/v1/analysis",
        json={"merged_transcription": str(merged)},
    )

    assert response.status_code == 400
    assert "validation" in response.json()["detail"].lower()


def test_server_analysis_endpoint_validates_missing_path() -> None:
    """The analysis route maps request path validation errors to 400."""
    response = TestClient(app).post(
        "/v1/analysis",
        json={"merged_transcription": "does-not-exist.json"},
    )

    assert response.status_code == 400
    assert "File does not exist" in response.json()["detail"]


def test_server_runs_endpoint_maps_transcription_request_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inference transport failures are reported as service unavailable."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    def fail_transcription(audio_path: Path, config: object) -> list[object]:
        raise requests.ConnectionError("inference server unavailable")

    monkeypatch.setattr("tara.pipeline.transcribe_audio_directory", fail_transcription)

    response = TestClient(app).post("/v1/runs", json={"audio_dir": str(audio_dir)})

    assert response.status_code == 503
    assert "inference server unavailable" in response.json()["detail"]


def test_server_runs_endpoint_accepts_merged_transcription(tmp_path: Path) -> None:
    """The general run endpoint supports the merged-transcription path."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")

    response = TestClient(app).post(
        "/v1/runs",
        json={"merged_transcription": str(merged)},
    )

    assert response.status_code == 200
    assert response.json()["attempts"] >= 1


def _merged_payload() -> str:
    """Return a merged transcription fixture that matches deterministic queries."""
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


def _raw_transcription_payload() -> str:
    """Return a single-file transcription fixture."""
    return json.dumps(
        {
            "text": "Le combat commence au temple.",
            "segments": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "text": "Le combat commence au temple.",
                }
            ],
            "language": "fr",
            "duration": 10.0,
            "model": "fixture",
        },
    )
