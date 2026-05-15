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
    assert args.context_path is None
    assert args.prior_context_path is None
    assert args.cursor_cli_probe is False


def test_parse_args_accepts_prior_context_and_cursor_probe(tmp_path: Path) -> None:
    """Optional prior markdown and Cursor CLI probe flags parse correctly."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    prior = tmp_path / "prior.md"
    prior.write_text("# Context\nLine.", encoding="utf-8")

    args = parse_args(
        [
            "--merged-transcription",
            str(merged),
            "--prior-context",
            str(prior),
            "--cursor-cli-probe",
        ],
    )

    assert args.prior_context_path == prior.resolve()
    assert args.cursor_cli_probe is True


def test_parse_args_accepts_context_without_requiring_existing_file(
    tmp_path: Path,
) -> None:
    """Context paths are validated by extension but may be missing at load time."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    missing_context = tmp_path / "context.md"

    args = parse_args(
        [
            "--merged-transcription",
            str(merged),
            "--context",
            str(missing_context),
            "--write-context-debug",
        ],
    )

    assert args.context_path == missing_context.resolve()
    assert args.write_context_debug is True


def test_parse_args_rejects_bad_context_extension(tmp_path: Path) -> None:
    """Context files are limited to markdown and text files."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")

    with pytest.raises(ArgumentParserError, match=".md or .txt"):
        parse_args(["--merged-transcription", str(merged), "--context", "bad.json"])


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
                    "context_path": "campaign.md",
                    "prior_context_path": "previous.md",
                    "llm": {"backend": "cursor_cli", "model": "Auto"},
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.analysis.pipeline == "blackboard_v1"
    assert config.analysis.context_path == "campaign.md"
    assert config.analysis.prior_context_path == "previous.md"
    assert config.analysis.llm.backend == "cursor_cli"
    assert config.analysis.llm.model == "Auto"
    assert config.analysis.llm.cursor_cli_probe is False


def test_load_config_reads_cursor_cli_probe_flag(tmp_path: Path) -> None:
    """JSON config may enable the optional Cursor CLI pipeline probe."""
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

    config = load_config(config_path)

    assert config.analysis.llm.cursor_cli_probe is True


def test_process_transcriptions_writes_merged_input(tmp_path: Path) -> None:
    """Processing keeps transcription compatibility and writes merged JSON."""
    output_dir = tmp_path / "transcriptions"
    output_dir.mkdir()
    (output_dir / "1-willygorn.json").write_text(
        _raw_transcription_payload("Le combat commence au temple."),
        encoding="utf-8",
    )
    (output_dir / "wameuh.json").write_text(
        _raw_transcription_payload("Après c'est Molnir."),
        encoding="utf-8",
    )
    (output_dir / "scene_analysis.json").write_text(
        json.dumps({"scenes": [{"text": "not a transcription"}]}),
        encoding="utf-8",
    )

    merged_path = process_transcriptions(output_dir, load_config(None))

    assert merged_path == output_dir / "merged_transcription.json"
    payload = json.loads(merged_path.read_text(encoding="utf-8"))
    assert payload["segments"][0]["text"] == "Le combat commence au temple."
    assert payload["segments"][0]["author"] == {
        "speaker": "willygorn",
        "source_file": "1-willygorn.json",
    }
    assert payload["segments"][1]["author"] == {
        "speaker": "wameuh",
        "source_file": "wameuh.json",
    }


def test_process_transcriptions_rerun_excludes_generated_artifacts(
    tmp_path: Path,
) -> None:
    """Processing reruns should ignore stale merged and analysis artifacts."""
    output_dir = tmp_path / "transcriptions"
    output_dir.mkdir()
    (output_dir / "1-willygorn.json").write_text(
        _raw_transcription_payload("Fresh source."),
        encoding="utf-8",
    )
    (output_dir / "merged_transcription.json").write_text(
        _raw_transcription_payload("Stale root merged."),
        encoding="utf-8",
    )
    old_dir = output_dir / "old"
    old_dir.mkdir()
    (old_dir / "merged_transcription.json").write_text(
        _raw_transcription_payload("Stale nested merged."),
        encoding="utf-8",
    )
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "runtime.json").write_text(
        _raw_transcription_payload("Analysis artifact."),
        encoding="utf-8",
    )
    scenes_dir = output_dir / "scenes"
    scenes_dir.mkdir()
    (scenes_dir / "scene.json").write_text(
        _raw_transcription_payload("Scene artifact."),
        encoding="utf-8",
    )

    merged_path = process_transcriptions(output_dir, load_config(None))

    assert merged_path == output_dir / "merged_transcription.json"
    payload = json.loads(merged_path.read_text(encoding="utf-8"))
    assert [segment["text"] for segment in payload["segments"]] == ["Fresh source."]
    assert payload["segments"][0]["author"]["speaker"] == "willygorn"


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
    assert payload["usage"]["llm_call_count"] == payload["summary"]["llm_call_count"]
    assert (
        payload["usage"]["llm_call_count"]
        == payload["acceptance"]["llm_call_count"]
    )
    assert (
        payload["usage"]["estimated_cost_usd"]
        == payload["acceptance"]["estimated_cost_usd"]
    )
    assert payload["usage"]["estimated_llm_tokens"] == 0
    assert payload["usage"]["backend"] == "deterministic"
    assert "Résumé exécutif" in result.session_summary_markdown_path.read_text(
        encoding="utf-8",
    )


def test_control_agent_loads_context_from_config_and_writes_debug(
    tmp_path: Path,
) -> None:
    """Config-relative context paths are traced and optionally debug-dumped."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    (tmp_path / "campaign.md").write_text(
        "willygorn plays Karknyr\napi_key=hidden",
        encoding="utf-8",
    )
    (tmp_path / "previous.md").write_text("Last time: temple.", encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "context_path": "campaign.md",
                    "prior_context_path": "previous.md",
                },
            },
        ),
        encoding="utf-8",
    )

    result = TaraControlAgent(
        parse_args(
            [
                "--merged-transcription",
                str(merged),
                "--config",
                str(config_path),
                "--write-context-debug",
            ],
        ),
    ).run()

    assert result.session_summary_json_path is not None
    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["traceability"]["context_path"].endswith("campaign.md")
    assert payload["traceability"]["prior_context_path"].endswith("previous.md")
    debug_path = merged.parent / "analysis" / "context_debug.md"
    assert debug_path.exists()
    debug_text = debug_path.read_text(encoding="utf-8")
    assert "willygorn plays Karknyr" in debug_text
    assert "api_key=hidden" not in debug_text
    assert "[redacted sensitive context line]" in debug_text


def test_control_agent_warns_and_continues_for_missing_config_context(
    tmp_path: Path,
) -> None:
    """Missing configured context files are warnings, not hard failures."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "context_path": "missing_context.md",
                    "prior_context_path": "missing_prior.txt",
                },
            },
        ),
        encoding="utf-8",
    )

    result = TaraControlAgent(
        parse_args(
            [
                "--merged-transcription",
                str(merged),
                "--config",
                str(config_path),
            ],
        ),
    ).run()

    assert result.warning_count == 2
    assert result.session_summary_json_path is not None
    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["traceability"]["context_path"] is None
    assert payload["traceability"]["prior_context_path"] is None


@pytest.mark.parametrize(
    ("field", "filename"),
    [
        ("context_path", "campaign.json"),
        ("prior_context_path", "previous.json"),
    ],
)
def test_control_agent_rejects_bad_config_context_extensions(
    tmp_path: Path,
    field: str,
    filename: str,
) -> None:
    """Config-origin context paths must also use md/txt extensions."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps({"analysis": {field: filename}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=".md, .txt|.txt, .md"):
        TaraControlAgent(
            parse_args(
                [
                    "--merged-transcription",
                    str(merged),
                    "--config",
                    str(config_path),
                ],
            ),
        ).run()


def test_cli_context_paths_override_config_context_paths(tmp_path: Path) -> None:
    """One-off CLI context paths should win over configured defaults."""
    merged = tmp_path / "merged_transcription.json"
    merged.write_text(_merged_payload(), encoding="utf-8")
    (tmp_path / "config_context.md").write_text("config context", encoding="utf-8")
    (tmp_path / "config_prior.md").write_text("config prior", encoding="utf-8")
    cli_context = tmp_path / "cli_context.md"
    cli_context.write_text("cli context", encoding="utf-8")
    cli_prior = tmp_path / "cli_prior.md"
    cli_prior.write_text("cli prior", encoding="utf-8")
    config_path = tmp_path / "configuration.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "context_path": "config_context.md",
                    "prior_context_path": "config_prior.md",
                },
            },
        ),
        encoding="utf-8",
    )

    result = TaraControlAgent(
        parse_args(
            [
                "--merged-transcription",
                str(merged),
                "--config",
                str(config_path),
                "--context",
                str(cli_context),
                "--prior-context",
                str(cli_prior),
                "--write-context-debug",
            ],
        ),
    ).run()

    assert result.session_summary_json_path is not None
    payload = json.loads(result.session_summary_json_path.read_text(encoding="utf-8"))
    assert payload["traceability"]["context_path"] == str(cli_context.resolve())
    assert payload["traceability"]["prior_context_path"] == str(cli_prior.resolve())
    debug_text = (merged.parent / "analysis" / "context_debug.md").read_text(
        encoding="utf-8",
    )
    assert "cli context" in debug_text
    assert "cli prior" in debug_text
    assert "config context" not in debug_text
    assert "config prior" not in debug_text


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


def _raw_transcription_payload(text: str = "Le combat commence au temple.") -> str:
    """Return a single-file transcription fixture."""
    return json.dumps(
        {
            "text": text,
            "segments": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "text": text,
                }
            ],
            "language": "fr",
            "duration": 10.0,
            "model": "fixture",
        },
    )
