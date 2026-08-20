"""Regression tests for merged transcription deduplication."""

from __future__ import annotations

import json
from pathlib import Path

from tara.config import load_config
from tara.transcription import process_transcriptions
from tara.schemas.registry import load_merged_transcription


def _raw_transcription_payload(text: str) -> str:
    """Build a minimal per-speaker transcription JSON payload."""
    return json.dumps(
        {
            "text": text,
            "segments": [{"start": 1.0, "end": 2.0, "text": text}],
            "language": "fr",
            "duration": 10.0,
            "model": "fixture",
        },
    )


def test_process_transcriptions_dedupes_json_and_yaml_copies(tmp_path: Path) -> None:
    """Each utterance appears once when JSON and YAML copies coexist."""
    output_dir = tmp_path / "transcriptions"
    output_dir.mkdir()
    text = "Le combat commence au temple."
    (output_dir / "1-willygorn.json").write_text(
        _raw_transcription_payload(text),
        encoding="utf-8",
    )
    (output_dir / "1-willygorn.yaml").write_text(
        _raw_transcription_payload(text),
        encoding="utf-8",
    )
    (output_dir / "merged_transcription.json").write_text(
        _raw_transcription_payload("Stale merged copy."),
        encoding="utf-8",
    )

    merged_path = process_transcriptions(output_dir, load_config(None))

    assert merged_path is not None
    payload = load_merged_transcription(merged_path)
    assert len(payload.segments) == 1
    assert payload.segments[0].text == text
    assert payload.segments[0].author is not None
    assert payload.segments[0].author.source_file == "1-willygorn.yaml"


def test_process_transcriptions_keeps_distinct_speakers(tmp_path: Path) -> None:
    """Distinct speaker tracks are preserved after canonical selection."""
    output_dir = tmp_path / "transcriptions"
    output_dir.mkdir()
    (output_dir / "1-willygorn.yaml").write_text(
        _raw_transcription_payload("Speaker one."),
        encoding="utf-8",
    )
    (output_dir / "2-wameuh.json").write_text(
        _raw_transcription_payload("Speaker two."),
        encoding="utf-8",
    )
    (output_dir / "2-wameuh.yaml").write_text(
        _raw_transcription_payload("Speaker two."),
        encoding="utf-8",
    )

    merged_path = process_transcriptions(output_dir, load_config(None))

    assert merged_path is not None
    payload = load_merged_transcription(merged_path)
    assert [segment.text for segment in payload.segments] == [
        "Speaker one.",
        "Speaker two.",
    ]
