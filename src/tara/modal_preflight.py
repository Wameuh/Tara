"""Preflight checks for Modal map-based transcription."""

from __future__ import annotations

import shutil
import sys

MODAL_APP_NAME = "tara-parakeet-inference"
MODAL_CLASS_NAME = "ParakeetTranscriber"
MODAL_METHOD_NAME = "transcribe_one"


def verify_modal_map_ready() -> None:
    """Verify Modal CLI auth and deployed ``ParakeetTranscriber`` are available.

    Raises:
        SystemExit: When a required Modal prerequisite is missing.
    """
    if shutil.which("uv") is None and shutil.which("modal") is None:
        print(
            "Error: Modal CLI not found. Install with `uv sync --extra deploy` "
            "or `pip install modal`.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        import modal
    except ImportError as exc:
        print(
            "Error: Modal Python package is not installed. "
            "Run `uv sync --extra deploy`.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    try:
        transcriber_cls = modal.Cls.from_name(MODAL_APP_NAME, MODAL_CLASS_NAME)
        if not hasattr(transcriber_cls(), MODAL_METHOD_NAME):
            raise AttributeError(
                f"Method '{MODAL_METHOD_NAME}' missing on "
                f"'{MODAL_APP_NAME}.{MODAL_CLASS_NAME}'",
            )
    except Exception as exc:
        print(
            "Error: Deployed Modal class "
            f"'{MODAL_APP_NAME}.{MODAL_CLASS_NAME}' is unavailable. "
            "Run `modal setup` and `modal deploy modal_inference.py`.",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> None:
    """CLI entrypoint for launcher preflight checks."""
    verify_modal_map_ready()


if __name__ == "__main__":
    main()
