"""Additional tests for _find_overlap_start to achieve 100% coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from inference_server.parakeet_utils import _find_overlap_start


def test_find_overlap_start_short_texts(tmp_path: Path) -> None:
    """Test _find_overlap_start returns 0 when texts are too short (lines 161-162)."""
    text1 = "one two"
    text2 = "three four"
    # Both texts have < 3 words (default min_overlap_words=3)
    overlap = _find_overlap_start(text1, text2, min_overlap_words=3)
    assert overlap == 0


def test_find_overlap_start_fuzzy_match_below_tolerance(tmp_path: Path) -> None:
    """Test _find_overlap_start returns 0 when fuzzy match is below tolerance (lines 184-186)."""
    text1 = "the quick brown fox jumps over"
    text2 = "completely different text here"
    # No overlap, similarity will be below tolerance
    overlap = _find_overlap_start(text1, text2, min_overlap_words=3, tolerance=0.8)
    assert overlap == 0


def test_find_overlap_start_fuzzy_match_meets_tolerance(tmp_path: Path) -> None:
    """Test _find_overlap_start returns overlap when fuzzy match meets tolerance (lines 184-190)."""
    text1 = "the quick brown fox jumps"
    text2 = "quick brown fox jumps over"  # 4 words overlap
    # Should find overlap with tolerance
    overlap = _find_overlap_start(text1, text2, min_overlap_words=3, tolerance=0.8)
    assert overlap >= 3


def test_find_overlap_start_best_match_len_below_min(tmp_path: Path) -> None:
    """Test _find_overlap_start returns 0 when best_match_len < min_overlap_words (line 189)."""
    text1 = "one two three four five"
    text2 = "six seven eight nine ten"
    # No overlap, best_match_len will be 0
    overlap = _find_overlap_start(text1, text2, min_overlap_words=3)
    assert overlap == 0


def test_find_overlap_start_max_check_limit(tmp_path: Path) -> None:
    """Test _find_overlap_start respects max_check limit (line 166)."""
    # Create texts longer than max_check (50 words)
    long_text1 = " ".join(["word"] * 60)
    long_text2 = " ".join(["word"] * 60)
    # Should still work but limit search to 50 words
    overlap = _find_overlap_start(long_text1, long_text2, min_overlap_words=3)
    # Should find overlap (all words are the same)
    assert overlap >= 3


def test_find_overlap_start_range_decrement(tmp_path: Path) -> None:
    """Test _find_overlap_start decrements through range correctly (line 171)."""
    text1 = "a b c d e f g h i j"
    text2 = "f g h i j k l m n o"
    # Overlap is "f g h i j" (5 words)
    overlap = _find_overlap_start(text1, text2, min_overlap_words=3)
    assert overlap == 5

