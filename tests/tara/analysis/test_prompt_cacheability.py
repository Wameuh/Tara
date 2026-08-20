"""Tests for compact boundary prompt serialization."""

from __future__ import annotations

from tara.analysis.models import (
    SegmentAuthor,
    TranscriptionSegment,
)
from tara.analysis.scenes.prompts import (
    boundary_user_prompt,
    format_transcription_segments,
    format_transcription_segments_compact,
)
from tara.schemas.merged_transcription import new_merged_transcription


def test_compact_boundary_prompt_is_smaller_than_verbose_format() -> None:
    """Compact boundary rows reduce prompt size without dropping long segments."""
    transcription = new_merged_transcription(
        text="",
        segments=[
            TranscriptionSegment(
                start=6.64,
                end=21.92,
                text="Alors comme on a eu le résumé.",
                author=SegmentAuthor(speaker="wameuh", source_file="2-wameuh.yaml"),
            ),
            TranscriptionSegment(
                start=22.0,
                end=23.0,
                text="ok",
                author=SegmentAuthor(speaker="wameuh", source_file="2-wameuh.yaml"),
            ),
        ],
    )
    verbose = format_transcription_segments(transcription)
    compact = format_transcription_segments_compact(transcription, min_text_chars=3)
    prompt = boundary_user_prompt(transcription)
    assert len(compact) < len(verbose)
    assert "segment_id=" not in compact
    assert "segment_id=" not in prompt
    assert "Alors comme on a eu le résumé." in compact
    assert "ok" not in compact
