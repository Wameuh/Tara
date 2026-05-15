"""Additional tests for _merge_overlapping_transcriptions to achieve 100% coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from inference_server.parakeet_utils import _merge_overlapping_transcriptions


def test_merge_overlapping_transcriptions_words_to_remove_zero(tmp_path: Path) -> None:
    """Test _merge_overlapping_transcriptions when words_to_remove is 0 (line 232)."""
    transcriptions = ["first chunk", "second chunk"]
    # With overlap_ratio=0, words_to_remove will be 0
    merged = _merge_overlapping_transcriptions(transcriptions, overlap_ratio=0.0)
    assert isinstance(merged, str)
    assert "first chunk" in merged
    assert "second chunk" in merged


def test_merge_overlapping_transcriptions_words_to_remove_exceeds_length(tmp_path: Path) -> None:
    """Test _merge_overlapping_transcriptions when words_to_remove >= len(curr_words) (line 233)."""
    transcriptions = ["a", "b"]  # Very short texts
    # With high overlap_ratio, words_to_remove might exceed length
    merged = _merge_overlapping_transcriptions(transcriptions, overlap_ratio=0.9)
    # Should fall back to appending the text (line 238-239)
    assert isinstance(merged, str)


def test_merge_overlapping_transcriptions_fallback_append(tmp_path: Path) -> None:
    """Test _merge_overlapping_transcriptions fallback path (lines 237-239)."""
    transcriptions = ["short", "text"]
    # Force fallback by having no overlap and words_to_remove logic fails
    merged = _merge_overlapping_transcriptions(transcriptions, overlap_ratio=0.0)
    # Should append both texts
    assert "short" in merged
    assert "text" in merged

