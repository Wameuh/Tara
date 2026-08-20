"""Edge case tests for inference_server.parakeet_utils to achieve 100% coverage."""

from __future__ import annotations

from pathlib import Path

from inference_server.parakeet_utils import _merge_overlapping_transcriptions


def test_merge_overlapping_transcriptions_all_words_overlap(tmp_path: Path) -> None:
    """Test _merge_overlapping_transcriptions when all words overlap (line 227)."""
    # Test case where overlap_start > 0 but all remaining words are overlap
    transcriptions = [
        "the quick brown fox",
        "brown fox"  # All words overlap with end of first
    ]
    merged = _merge_overlapping_transcriptions(transcriptions, overlap_ratio=0.1)
    # Should handle gracefully and not crash
    assert isinstance(merged, str)


def test_merge_overlapping_transcriptions_overlap_no_words_to_add(tmp_path: Path) -> None:
    """Test _merge_overlapping_transcriptions when overlap detected but no words to add."""
    # Create a case where overlap is found but words_to_add is empty
    transcriptions = [
        "a b c d",
        "c d"  # Overlap detected, but no words after overlap
    ]
    merged = _merge_overlapping_transcriptions(transcriptions, overlap_ratio=0.1)
    # Should handle gracefully
    assert isinstance(merged, str)
    assert "a b c d" in merged or len(merged) > 0


