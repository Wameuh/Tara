"""Tests for post-run reporting helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from tara.run_reporting import (
    emit_llm_usage_from_run_result,
    fetch_modal_billing_usd,
    format_combined_cost_summary,
    format_llm_usage_summary,
    format_run_timing_summary,
)


def test_format_llm_usage_summary_reads_session_summary(tmp_path: Path) -> None:
    """Usage lines are built from session_summary.yaml usage counters."""
    summary_path = tmp_path / "session_summary.yaml"
    summary_path.write_text(
        json.dumps(
            {
                "usage": {
                    "llm_call_count": 27,
                    "estimated_llm_tokens": 702791,
                    "estimated_cost_usd": 0.452964,
                    "backend": "cursor_cli",
                },
            },
        ),
        encoding="utf-8",
    )

    lines = format_llm_usage_summary(summary_path)

    assert lines[0] == "LLM usage summary:"
    assert lines[1] == "  Estimated cost: $0.4530 USD"
    assert "702,791" in lines[2]
    assert lines[3] == "  Calls: 27"
    assert lines[4] == "  Backend: cursor_cli"


def test_emit_llm_usage_from_run_result_prints_summary(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Batch launchers can resolve usage from a saved Tara run JSON file."""
    summary_path = tmp_path / "analysis" / "session_summary.yaml"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps({"usage": {"llm_call_count": 1, "estimated_cost_usd": 0.01}}),
        encoding="utf-8",
    )
    run_result_path = tmp_path / "run.yaml"
    run_result_path.write_text(
        json.dumps({"session_summary_json_path": str(summary_path)}),
        encoding="utf-8",
    )

    assert emit_llm_usage_from_run_result(run_result_path) is True
    captured = capsys.readouterr()
    assert "Estimated cost: $0.0100 USD" in captured.out
    assert "Per-job estimated total: $0.0100 USD" in captured.out


def test_format_run_timing_summary_includes_global_and_llm_totals(
    tmp_path: Path,
) -> None:
    """Timing lines include global wall clock and per-stage latency summaries."""
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "usage_report.yaml").write_text(
        """
total_calls: 2
total_llm_latency_ms: 90000
records:
  - stage: scenes.description
    purpose: analysis.scenes.describe
    calls: 2
    latency_p50_ms: 45000
    latency_total_ms: 90000
""",
        encoding="utf-8",
    )

    lines = format_run_timing_summary(
        analysis_dir,
        global_wall_clock_seconds=1125.0,
        tara_wall_clock_seconds=932.0,
    )

    assert lines[0] == "Run timing:"
    assert any("Global wall clock" in line and "18m 45s" in line for line in lines)
    assert any("Tara process" in line and "15m 32s" in line for line in lines)
    assert any("Total: 2 LLM call(s), 1m 30s LLM compute" in line for line in lines)
    assert any("scenes.describe" in line for line in lines)


def test_format_combined_cost_summary_keeps_modal_buckets_separate() -> None:
    """Shared Modal buckets must never be attributed to one concurrent job."""
    lines = format_combined_cost_summary(
        llm_cost_usd=0.5228,
        modal_cost_usd=0.2064,
        modal_bucket_count=1,
    )

    assert lines[0] == "Cost summary:"
    assert lines[1] == "  LLM (Cursor CLI): $0.5228 USD"
    assert lines[2] == "  Per-job estimated total: $0.5228 USD"
    assert "Modal shared billing reconciliation: $0.2064 USD" in lines[3]
    assert lines[4] == "  Modal shared buckets are not attributed to this job."


def test_fetch_modal_billing_usd_filters_overlapping_buckets(
    tmp_path: Path,
) -> None:
    """Modal billing should only include hourly buckets overlapping the run."""
    billing_payload = [
        {
            "interval_start": "2026-07-04T16:00:00+03:00",
            "cost": "0.10",
        },
        {
            "interval_start": "2026-07-04T17:00:00+03:00",
            "cost": "0.20",
        },
        {
            "interval_start": "2026-07-04T18:00:00+03:00",
            "cost": "0.30",
        },
    ]
    completed = MagicMock(returncode=0, stdout=json.dumps(billing_payload))
    run_start = datetime.fromisoformat("2026-07-04T17:27:00+03:00")
    run_end = datetime.fromisoformat("2026-07-04T17:45:00+03:00")

    with patch("tara.run_reporting.subprocess.run", return_value=completed):
        cost, buckets = fetch_modal_billing_usd(
            run_started_iso=run_start.isoformat(),
            run_ended_iso=run_end.isoformat(),
            project_dir=tmp_path,
        )

    assert cost == 0.2
    assert buckets == 1
