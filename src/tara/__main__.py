"""Executable module for `python -m tara`."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from tara.cli import ArgumentParserError, parse_args
from tara.pipeline import run_from_args
from tara.run_reporting import emit_llm_usage_summary
from tara.yaml_utils import to_yaml


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Tara CLI.

    Args:
        argv: Optional CLI argument sequence.

    Returns:
        Process exit code.
    """
    try:
        args = parse_args(argv)
        result = run_from_args(args)
    except ArgumentParserError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Tara run failed: {exc}", file=sys.stderr)
        return 1
    print(to_yaml(result.to_dict()))
    if os.environ.get("TARA_RUN_REPORTING") != "bat":
        emit_llm_usage_summary(result.session_summary_json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
