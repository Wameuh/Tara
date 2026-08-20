"""Human-readable post-run reporting for Tara CLI and batch launchers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from tara.yaml_utils import from_yaml, load_yaml_or_json


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as a compact human-readable string.

    Args:
        seconds: Elapsed time in seconds.

    Returns:
        A string such as ``12m 34s`` or ``1h 05m 09s``.
    """
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_run_timing_summary(
    analysis_output_dir: Path | None,
    *,
    global_wall_clock_seconds: float | None = None,
    tara_wall_clock_seconds: float | None = None,
) -> list[str]:
    """Build human-readable timing lines from wall clock and usage artifacts.

    Args:
        analysis_output_dir: Directory containing ``usage_report.yaml``.
        global_wall_clock_seconds: End-to-end elapsed seconds for the full batch run.
        tara_wall_clock_seconds: Elapsed seconds for the ``python -m tara`` subprocess.

    Returns:
        Non-empty lines when timing data is available; otherwise an empty list.
    """
    lines: list[str] = []
    if global_wall_clock_seconds is not None and global_wall_clock_seconds >= 0:
        lines.extend(
            [
                "Run timing:",
                (
                    "  Global wall clock: "
                    f"{format_duration(global_wall_clock_seconds)} "
                    "(preflight, transcription, analysis)"
                ),
            ]
        )
    elif tara_wall_clock_seconds is not None and tara_wall_clock_seconds >= 0:
        lines.extend(
            [
                "Run timing:",
                f"  Wall clock: {format_duration(tara_wall_clock_seconds)}",
            ]
        )
    if (
        tara_wall_clock_seconds is not None
        and tara_wall_clock_seconds >= 0
        and global_wall_clock_seconds is not None
        and global_wall_clock_seconds >= 0
        and abs(tara_wall_clock_seconds - global_wall_clock_seconds) >= 1.0
    ):
        lines.append(f"  Tara process: {format_duration(tara_wall_clock_seconds)}")
    usage_path = (
        analysis_output_dir / "usage_report.yaml"
        if analysis_output_dir is not None
        else None
    )
    if usage_path is None or not usage_path.is_file():
        if not lines and tara_wall_clock_seconds is not None:
            lines.extend(
                [
                    "Run timing:",
                    f"  Wall clock: {format_duration(tara_wall_clock_seconds)}",
                ]
            )
        return lines
    try:
        payload = load_yaml_or_json(usage_path)
    except (OSError, ValueError):
        return lines
    if not isinstance(payload, dict):
        return lines
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        return lines
    if not lines:
        lines.append("Run timing:")
    lines.append("  LLM stages (per-call latency; overlaps when parallel):")
    stage_lines: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        stage = str(record.get("stage") or "unknown")
        purpose = str(record.get("purpose") or stage)
        calls = _to_int(record.get("calls"))
        p50_ms = record.get("latency_p50_ms")
        if calls <= 0 or p50_ms is None:
            continue
        label = purpose.removeprefix("analysis.")
        stage_lines.append(
            f"    {label}: {calls} call(s), "
            f"p50 {format_duration(float(p50_ms) / 1000.0)}"
        )
    if not stage_lines:
        return lines
    lines.extend(stage_lines)
    total_calls = _to_int(payload.get("total_calls"))
    if total_calls <= 0:
        total_calls = sum(
            _to_int(record.get("calls"))
            for record in records
            if isinstance(record, dict)
        )
    total_llm_latency_ms = _total_llm_latency_ms(payload, records)
    lines.append(
        "  Total: "
        f"{total_calls} LLM call(s), "
        f"{format_duration(total_llm_latency_ms / 1000.0)} LLM compute"
    )
    return lines


def format_llm_usage_summary(session_summary_json_path: Path | None) -> list[str]:
    """Build human-readable LLM usage lines from a session summary artifact.

    Args:
        session_summary_json_path: Path to ``session_summary.yaml`` (or legacy JSON).

    Returns:
        Non-empty lines when LLM usage is available; otherwise an empty list.
    """
    if session_summary_json_path is None or not session_summary_json_path.is_file():
        return []
    try:
        payload = load_yaml_or_json(session_summary_json_path)
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return []
    calls = _to_int(usage.get("llm_call_count"))
    if calls <= 0:
        return []
    cost = float(usage.get("estimated_cost_usd") or 0.0)
    tokens = _to_int(usage.get("estimated_llm_tokens"))
    backend = str(usage.get("backend") or "unknown")
    return [
        "LLM usage summary:",
        f"  Estimated cost: ${cost:.4f} USD",
        f"  Tokens: {tokens:,}",
        f"  Calls: {calls}",
        f"  Backend: {backend}",
    ]


def format_combined_cost_summary(
    *,
    llm_cost_usd: float | None,
    modal_cost_usd: float | None,
    modal_bucket_count: int = 0,
) -> list[str]:
    """Report per-run estimates separately from shared Modal billing buckets.

    Args:
        llm_cost_usd: Estimated LLM cost for the Tara analysis run.
        modal_cost_usd: Shared Modal billing for overlapping hours, used only for
            operational reconciliation.
        modal_bucket_count: Number of hourly Modal billing buckets included.

    Returns:
        Non-empty lines when at least one cost component is available.
    """
    if llm_cost_usd is None and modal_cost_usd is None:
        return []
    lines = ["Cost summary:"]
    if llm_cost_usd is not None:
        lines.append(f"  LLM (Cursor CLI): ${llm_cost_usd:.4f} USD")
        lines.append(f"  Per-job estimated total: ${llm_cost_usd:.4f} USD")
    if modal_cost_usd is not None:
        bucket_note = (
            f" ({modal_bucket_count} hourly bucket(s) overlapping this run)"
            if modal_bucket_count > 0
            else ""
        )
        lines.append(
            "  Modal shared billing reconciliation: "
            f"${modal_cost_usd:.4f} USD{bucket_note}"
        )
        lines.append("  Modal shared buckets are not attributed to this job.")
    return lines


def fetch_modal_billing_usd(
    *,
    run_started_iso: str,
    run_ended_iso: str,
    project_dir: Path,
) -> tuple[float | None, int]:
    """Fetch shared Modal billing buckets for operational reconciliation only.

    Args:
        run_started_iso: Run start timestamp in ISO-8601 format.
        run_ended_iso: Run end timestamp in ISO-8601 format.
        project_dir: Tara project directory passed to ``uv run --project``.

    Returns:
        Tuple of ``(estimated_usd, bucket_count)``. ``estimated_usd`` is ``None``
        when Modal billing cannot be retrieved.
    """
    try:
        run_start = datetime.fromisoformat(run_started_iso)
        run_end = datetime.fromisoformat(run_ended_iso)
    except ValueError:
        return None, 0
    if run_end < run_start:
        run_start, run_end = run_end, run_start
    query_start = run_start.astimezone(UTC).date().isoformat()
    query_end = (run_end.astimezone(UTC).date() + timedelta(days=1)).isoformat()
    command = [
        "uv",
        "run",
        "--project",
        str(project_dir),
        "--extra",
        "deploy",
        "modal",
        "billing",
        "report",
        "--start",
        query_start,
        "--end",
        query_end,
        "--resolution",
        "h",
        "--tz",
        "local",
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None, 0
    if completed.returncode != 0:
        return None, 0
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, 0
    if not isinstance(payload, list):
        return None, 0
    total_cost = 0.0
    bucket_count = 0
    for row in payload:
        if not isinstance(row, dict):
            continue
        interval_start_raw = row.get("interval_start")
        cost_raw = row.get("cost")
        if not isinstance(interval_start_raw, str):
            continue
        try:
            interval_start = datetime.fromisoformat(interval_start_raw)
            interval_end = interval_start + timedelta(hours=1)
            cost = float(cost_raw)
        except (TypeError, ValueError):
            continue
        if interval_start < run_end and interval_end > run_start:
            total_cost += cost
            bucket_count += 1
    if bucket_count == 0:
        return 0.0, 0
    return total_cost, bucket_count


def _llm_cost_usd(session_summary_json_path: Path | None) -> float | None:
    """Return the estimated LLM cost from a session summary artifact."""
    if session_summary_json_path is None or not session_summary_json_path.is_file():
        return None
    try:
        payload = load_yaml_or_json(session_summary_json_path)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    calls = _to_int(usage.get("llm_call_count"))
    if calls <= 0:
        return None
    return float(usage.get("estimated_cost_usd") or 0.0)


def emit_llm_usage_summary(
    session_summary_json_path: Path | None,
    *,
    stream: TextIO | None = None,
) -> bool:
    """Print LLM usage summary lines when analysis usage is available.

    Args:
        session_summary_json_path: Path to ``session_summary.yaml`` (or legacy JSON).
        stream: Output stream. Defaults to stderr so stdout stays YAML-only.

    Returns:
        True when a summary was printed.
    """
    lines = format_llm_usage_summary(session_summary_json_path)
    if not lines:
        return False
    output = stream if stream is not None else sys.stderr
    print("", file=output)
    for line in lines:
        print(line, file=output)
    return True


def emit_run_timing_summary(
    run_result_path: Path,
    *,
    global_wall_clock_seconds: float | None = None,
    tara_wall_clock_seconds: float | None = None,
    stream: TextIO | None = None,
) -> bool:
    """Print timing summary lines from a Tara run result file.

    Args:
        run_result_path: Path to the YAML emitted by ``python -m tara``.
        global_wall_clock_seconds: End-to-end elapsed seconds for the batch run.
        tara_wall_clock_seconds: Elapsed seconds for the Tara subprocess.
        stream: Output stream. Defaults to stdout.

    Returns:
        True when a summary was printed.
    """
    if not run_result_path.is_file():
        return False
    try:
        payload = from_yaml(run_result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    analysis_dir_raw = payload.get("analysis_output_dir")
    analysis_output_dir = (
        Path(analysis_dir_raw)
        if isinstance(analysis_dir_raw, str) and analysis_dir_raw.strip()
        else None
    )
    lines = format_run_timing_summary(
        analysis_output_dir,
        global_wall_clock_seconds=global_wall_clock_seconds,
        tara_wall_clock_seconds=tara_wall_clock_seconds,
    )
    if not lines:
        return False
    output = stream if stream is not None else sys.stdout
    print("", file=output)
    for line in lines:
        print(line, file=output)
    return True


def emit_llm_usage_from_run_result(
    run_result_path: Path,
    *,
    global_wall_clock_seconds: float | None = None,
    tara_wall_clock_seconds: float | None = None,
    include_modal_billing: bool = False,
    run_started_iso: str | None = None,
    run_ended_iso: str | None = None,
    project_dir: Path | None = None,
    stream: TextIO | None = None,
) -> bool:
    """Print timing and usage summaries from a saved Tara run result file.

    Args:
        run_result_path: Path to the YAML emitted by ``python -m tara``.
        global_wall_clock_seconds: End-to-end elapsed seconds for the batch run.
        tara_wall_clock_seconds: Elapsed seconds for the Tara subprocess.
        include_modal_billing: Whether to include Modal transcription cost.
        run_started_iso: ISO timestamp for the batch run start.
        run_ended_iso: ISO timestamp for the batch run end.
        project_dir: Tara project directory for Modal billing lookup.
        stream: Output stream. Defaults to stdout.

    Returns:
        True when at least one summary block was printed.
    """
    if not run_result_path.is_file():
        return False
    try:
        payload = from_yaml(run_result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    summary_path_raw = payload.get("session_summary_json_path")
    summary_path = (
        Path(summary_path_raw)
        if isinstance(summary_path_raw, str) and summary_path_raw.strip()
        else None
    )
    output = stream if stream is not None else sys.stdout
    printed = emit_run_timing_summary(
        run_result_path,
        global_wall_clock_seconds=global_wall_clock_seconds,
        tara_wall_clock_seconds=tara_wall_clock_seconds,
        stream=output,
    )
    usage_printed = False
    if summary_path is not None:
        usage_printed = emit_llm_usage_summary(summary_path, stream=output)
    modal_cost: float | None = None
    modal_buckets = 0
    if (
        include_modal_billing
        and run_started_iso
        and run_ended_iso
        and project_dir is not None
    ):
        modal_cost, modal_buckets = fetch_modal_billing_usd(
            run_started_iso=run_started_iso,
            run_ended_iso=run_ended_iso,
            project_dir=project_dir,
        )
    llm_cost = _llm_cost_usd(summary_path)
    combined_lines = format_combined_cost_summary(
        llm_cost_usd=llm_cost,
        modal_cost_usd=modal_cost,
        modal_bucket_count=modal_buckets,
    )
    if combined_lines:
        print("", file=output)
        for line in combined_lines:
            print(line, file=output)
        printed = True
    return printed or usage_printed


def _total_llm_latency_ms(payload: dict[str, Any], records: list[Any]) -> int:
    """Resolve total LLM latency from summary fields or per-record data."""
    explicit = payload.get("total_llm_latency_ms")
    if explicit is not None:
        return _to_int(explicit)
    total = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        record_total = record.get("latency_total_ms")
        if record_total is not None:
            total += _to_int(record_total)
            continue
        calls = _to_int(record.get("calls"))
        p50_ms = record.get("latency_p50_ms")
        if calls > 0 and p50_ms is not None:
            total += int(float(p50_ms) * calls)
    return total


def _to_int(value: Any) -> int:
    """Convert usage counters to non-negative integers."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_optional_float(value: str | None) -> float | None:
    """Parse an optional floating-point CLI argument."""
    if value is None or not str(value).strip():
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for batch launchers."""
    parser = argparse.ArgumentParser(description="Print Tara post-run summaries.")
    parser.add_argument("run_result", type=Path, help="Path to the Tara run YAML.")
    parser.add_argument(
        "--global-seconds",
        type=float,
        default=None,
        help="End-to-end elapsed seconds for the full batch run.",
    )
    parser.add_argument(
        "--tara-seconds",
        type=float,
        default=None,
        help="Elapsed seconds for the python -m tara subprocess.",
    )
    parser.add_argument(
        "--include-modal-billing",
        action="store_true",
        help="Include Modal transcription cost in the combined summary.",
    )
    parser.add_argument(
        "--run-started-iso",
        default=None,
        help="ISO-8601 timestamp when the batch run started.",
    )
    parser.add_argument(
        "--run-ended-iso",
        default=None,
        help="ISO-8601 timestamp when the batch run ended.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Tara project directory for Modal billing lookup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for batch launchers.

    Args:
        argv: Optional argument list.

    Returns:
        Process exit code.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    global_seconds = args.global_seconds
    tara_seconds = args.tara_seconds
    if global_seconds is None:
        global_seconds = _parse_optional_float(
            os.environ.get("TARA_GLOBAL_ELAPSED_SECONDS")
        )
    if tara_seconds is None:
        tara_seconds = _parse_optional_float(os.environ.get("TARA_RUN_ELAPSED_SECONDS"))
    project_dir = args.project_dir
    if project_dir is None:
        project_env = os.environ.get("TARA_PROJECT_DIR")
        project_dir = Path(project_env) if project_env else None
    emit_llm_usage_from_run_result(
        args.run_result,
        global_wall_clock_seconds=global_seconds,
        tara_wall_clock_seconds=tara_seconds,
        include_modal_billing=args.include_modal_billing,
        run_started_iso=args.run_started_iso or os.environ.get("TARA_RUN_STARTED_ISO"),
        run_ended_iso=args.run_ended_iso or os.environ.get("TARA_RUN_ENDED_ISO"),
        project_dir=project_dir,
        stream=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
