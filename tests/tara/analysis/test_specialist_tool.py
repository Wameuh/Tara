"""Tests for the tara-evidence excerpt script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_transcription(path: Path) -> None:
    """Write a minimal merged transcription JSON artifact."""
    path.write_text(
        json.dumps(
            {
                "text": "line",
                "segments": [
                    {"start": 10.0, "end": 12.0, "text": "Inside range."},
                    {"start": 100.0, "end": 102.0, "text": "Outside range."},
                ],
            },
        ),
        encoding="utf-8",
    )


def test_get_excerpt_script_filters_by_timestamp(tmp_path: Path) -> None:
    """The excerpt script should print only overlapping segments."""
    transcription_path = tmp_path / "merged_transcription.json"
    _write_transcription(transcription_path)
    script_path = (
        Path(__file__).resolve().parents[3]
        / ".cursor"
        / "skills"
        / "tara-evidence"
        / "scripts"
        / "get_excerpt.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--transcription",
            str(transcription_path.resolve()),
            "--start",
            "11",
            "--end",
            "12",
            "--pad",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Inside range." in result.stdout
    assert "Outside range." not in result.stdout
