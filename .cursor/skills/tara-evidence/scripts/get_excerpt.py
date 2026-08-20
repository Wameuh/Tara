"""Fetch timestamp-bounded transcript excerpts from merged transcription files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_tara_import_path() -> None:
    """Add TaraRepo ``src`` to ``sys.path`` for standalone script execution."""
    repo_root = Path(__file__).resolve().parents[4]
    src_path = repo_root / "src"
    if src_path.is_dir():
        src_text = str(src_path)
        if src_text not in sys.path:
            sys.path.insert(0, src_text)


_ensure_tara_import_path()

from tara.analysis.models import (  # noqa: E402 - standalone sys.path bootstrap above
    MergedTranscription,
    TranscriptionSegment,
)
from tara.schemas.registry import (  # noqa: E402 - standalone sys.path bootstrap above
    load_merged_transcription,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for excerpt retrieval.

    Args:
        argv: Optional argument vector for tests.

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Print transcript rows from a merged transcription file for a "
            "timestamp range."
        ),
    )
    parser.add_argument(
        "--transcription",
        required=True,
        type=Path,
        help="Absolute path to merged_transcription.yaml or legacy JSON.",
    )
    parser.add_argument(
        "--start",
        required=True,
        type=float,
        help="Range start in seconds.",
    )
    parser.add_argument(
        "--end",
        required=True,
        type=float,
        help="Range end in seconds.",
    )
    parser.add_argument(
        "--pad",
        type=float,
        default=15.0,
        help="Extra seconds of context on both sides of the range.",
    )
    return parser.parse_args(argv)


def load_transcription(path: Path) -> MergedTranscription:
    """Load a merged transcription artifact from disk.

    Args:
        path: Path to YAML or JSON merged transcription.

    Returns:
        Parsed merged transcription.

    Raises:
        ValueError: If the file does not contain a valid transcription object.
    """
    return load_merged_transcription(path)


def filter_segments_by_time_range(
    segments: list[TranscriptionSegment],
    *,
    start: float,
    end: float,
    pad: float,
) -> list[tuple[int, TranscriptionSegment]]:
    """Return indexed segments overlapping a padded timestamp range.

    Args:
        segments: Source transcript segments.
        start: Range start in seconds.
        end: Range end in seconds.
        pad: Padding added on both sides.

    Returns:
        Indexed segments whose time span overlaps the padded range.
    """
    window_start = max(0.0, start - pad)
    window_end = end + pad
    selected: list[tuple[int, TranscriptionSegment]] = []
    for index, segment in enumerate(segments):
        if not segment.text.strip():
            continue
        if segment.end < window_start or segment.start > window_end:
            continue
        selected.append((index, segment))
    return selected


def format_segment_row(index: int, segment: TranscriptionSegment) -> str:
    """Render one compact transcript row for LLM consumption.

    Args:
        index: Global segment index.
        segment: Transcript segment.

    Returns:
        Compact row string.
    """
    speaker = segment.author.speaker if segment.author else "unknown"
    text = " ".join(segment.text.split())
    return f"[{index} {segment.start:.1f}-{segment.end:.1f}s {speaker}] {text}"


def render_excerpt(
    transcription: MergedTranscription,
    *,
    start: float,
    end: float,
    pad: float,
) -> str:
    """Render transcript rows for one timestamp range.

    Args:
        transcription: Canonical merged transcription.
        start: Range start in seconds.
        end: Range end in seconds.
        pad: Padding added on both sides.

    Returns:
        Newline-delimited excerpt text.
    """
    rows = filter_segments_by_time_range(
        transcription.segments,
        start=start,
        end=end,
        pad=pad,
    )
    return "\n".join(format_segment_row(index, segment) for index, segment in rows)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector for tests.

    Returns:
        Process exit code.
    """
    args = parse_args(argv)
    transcription_path = args.transcription.resolve()
    if not transcription_path.is_file():
        print(f"Transcription file not found: {transcription_path}", file=sys.stderr)
        return 1
    if args.end < args.start:
        print("--end must be greater than or equal to --start.", file=sys.stderr)
        return 1
    transcription = load_transcription(transcription_path)
    print(
        render_excerpt(
            transcription,
            start=args.start,
            end=args.end,
            pad=args.pad,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
