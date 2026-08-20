"""Compare two Tara usage_report.yaml files."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

from tara.yaml_utils import load_yaml_or_json


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Compare two usage_report.yaml files.")
    parser.add_argument("baseline", type=Path, help="Baseline usage report path.")
    parser.add_argument("candidate", type=Path, help="Candidate usage report path.")
    return parser.parse_args(argv)


def _summary(path: Path) -> Mapping[str, Any]:
    """Load one usage report summary mapping."""
    payload = load_yaml_or_json(path)
    if not isinstance(payload, Mapping):
        raise ValueError(f"Usage report must be a mapping: {path}")
    return payload


def compare_reports(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
    """Return a human-readable diff summary."""
    lines = [
        "Usage report comparison",
        f"baseline_calls={baseline.get('total_calls', 0)} "
        f"candidate_calls={candidate.get('total_calls', 0)}",
        f"baseline_tokens={baseline.get('total_tokens', 0)} "
        f"candidate_tokens={candidate.get('total_tokens', 0)}",
        f"baseline_cost_usd={baseline.get('total_cost_usd', 0.0)} "
        f"candidate_cost_usd={candidate.get('total_cost_usd', 0.0)}",
    ]
    baseline_records = {
        (row.get("stage"), row.get("purpose"), row.get("model")): row
        for row in baseline.get("records", [])
        if isinstance(row, Mapping)
    }
    candidate_records = {
        (row.get("stage"), row.get("purpose"), row.get("model")): row
        for row in candidate.get("records", [])
        if isinstance(row, Mapping)
    }
    for key in sorted(set(baseline_records) | set(candidate_records)):
        base_row = baseline_records.get(key, {})
        cand_row = candidate_records.get(key, {})
        lines.append(
            f"{key}: tokens {base_row.get('total_tokens', 0)} -> "
            f"{cand_row.get('total_tokens', 0)}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    print(compare_reports(_summary(args.baseline), _summary(args.candidate)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
