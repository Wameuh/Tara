"""Command-line parsing for the standalone Tara application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class ArgumentParserError(Exception):
    """Raised when CLI arguments are invalid."""


class _TaraArgumentParser(argparse.ArgumentParser):
    """Argument parser that raises a testable exception on errors."""

    def error(self, message: str) -> None:
        """Raise `ArgumentParserError` instead of exiting the interpreter."""
        raise ArgumentParserError(message)


@dataclass(frozen=True, slots=True)
class TaraArgs:
    """Validated command-line arguments for a Tara run."""

    audio_dir: Path | None = None
    merged_transcription: Path | None = None
    config: Path | None = None
    skip_analysis: bool = False
    analysis_backend: str | None = None
    analysis_model: str | None = None
    start_from: str | None = None
    blackboard_path: Path | None = None
    analysis_plan_path: Path | None = None
    prior_context_path: Path | None = None
    cursor_cli_probe: bool = False


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone Tara CLI parser."""
    parser = _TaraArgumentParser(
        description="Tara - standalone transcription and blackboard analysis",
    )
    parser.add_argument("--audio-dir", metavar="DIR", help="Audio directory to process")
    parser.add_argument(
        "--merged-transcription",
        metavar="FILE",
        help="Existing merged_transcription.json to analyze",
    )
    parser.add_argument("--config", metavar="FILE", help="JSON configuration file")
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Run transcription/processing only",
    )
    parser.add_argument(
        "--analysis-backend",
        choices=["api", "cursor_cli"],
        help="Override analysis LLM backend for future LLM-assisted stages",
    )
    parser.add_argument(
        "--analysis-model",
        help="Override analysis LLM model for future LLM-assisted stages",
    )
    parser.add_argument(
        "--start-from",
        choices=["transcription", "processing", "evidence-index"],
        help="Start from a specific pipeline stage",
    )
    parser.add_argument(
        "--blackboard",
        dest="blackboard_path",
        metavar="FILE",
        help="Optional path for blackboard debug JSON",
    )
    parser.add_argument(
        "--analysis-plan",
        dest="analysis_plan_path",
        metavar="FILE",
        help="Optional path for analysis plan debug JSON",
    )
    parser.add_argument(
        "--prior-context",
        metavar="FILE",
        help=(
            "Optional markdown (e.g. prior session summary) included in the "
            "Cursor CLI pipeline probe stdin when the probe runs"
        ),
    )
    parser.add_argument(
        "--cursor-cli-probe",
        action="store_true",
        help="Force the one-shot Cursor CLI probe before analysis (cursor_cli backend)",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> TaraArgs:
    """Parse and validate command-line arguments.

    Args:
        argv: Optional argument sequence. `None` uses `sys.argv`.

    Returns:
        Validated Tara arguments.

    Raises:
        ArgumentParserError: If inputs are missing or invalid.
    """
    namespace = build_parser().parse_args(argv)
    audio_dir = _optional_path(namespace.audio_dir)
    merged_transcription = _optional_path(namespace.merged_transcription)
    config = _optional_path(namespace.config)
    blackboard_path = _optional_path(namespace.blackboard_path)
    analysis_plan_path = _optional_path(namespace.analysis_plan_path)
    prior_context_path = _optional_path(namespace.prior_context)

    if audio_dir is None and merged_transcription is None:
        raise ArgumentParserError(
            "Provide exactly one input: --audio-dir or --merged-transcription.",
        )
    if audio_dir is not None and merged_transcription is not None:
        raise ArgumentParserError(
            "Only one input can be provided: --audio-dir or --merged-transcription.",
        )
    if audio_dir is not None and (not audio_dir.exists() or not audio_dir.is_dir()):
        raise ArgumentParserError(f"Audio directory does not exist: {audio_dir}")
    if merged_transcription is not None and (
        not merged_transcription.exists() or not merged_transcription.is_file()
    ):
        raise ArgumentParserError(
            f"Merged transcription file does not exist: {merged_transcription}",
        )
    if config is not None and (not config.exists() or not config.is_file()):
        raise ArgumentParserError(f"Configuration file does not exist: {config}")
    if prior_context_path is not None and (
        not prior_context_path.exists() or not prior_context_path.is_file()
    ):
        raise ArgumentParserError(
            f"Prior context file does not exist: {prior_context_path}",
        )

    return TaraArgs(
        audio_dir=audio_dir.resolve() if audio_dir else None,
        merged_transcription=(
            merged_transcription.resolve() if merged_transcription else None
        ),
        config=config.resolve() if config else None,
        skip_analysis=bool(namespace.skip_analysis),
        analysis_backend=namespace.analysis_backend,
        analysis_model=namespace.analysis_model,
        start_from=namespace.start_from,
        blackboard_path=blackboard_path.resolve() if blackboard_path else None,
        analysis_plan_path=analysis_plan_path.resolve() if analysis_plan_path else None,
        prior_context_path=(
            prior_context_path.resolve() if prior_context_path else None
        ),
        cursor_cli_probe=bool(namespace.cursor_cli_probe),
    )


def _optional_path(value: str | None) -> Path | None:
    """Convert an optional string to a `Path`."""
    return Path(value) if value else None
