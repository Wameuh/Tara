"""Executable module for `python -m tara`."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from tara.cli import ArgumentParserError, parse_args
from tara.pipeline import run_from_args


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
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
