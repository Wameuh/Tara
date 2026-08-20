"""Tests for extract functions to achieve 100% coverage."""

from __future__ import annotations

from pathlib import Path

from inference_server.parakeet_utils import (
    _extract_text_from_result,
    _extract_timestamps_from_result,
)


def test_extract_text_from_result_not_string_not_hasattr(tmp_path: Path) -> None:
    """Test _extract_text_from_result with object that's not string and has no text attribute (line 119)."""
    class NoTextObject:
        def __str__(self) -> str:
            return "string representation"

    result = NoTextObject()
    text = _extract_text_from_result(result)
    assert text == "string representation"


def test_extract_timestamps_from_result_not_dict(tmp_path: Path) -> None:
    """Test _extract_timestamps_from_result when timestamp is not a dict (line 134)."""
    class ResultWithNonDictTimestamp:
        def __init__(self) -> None:
            self.timestamp = "not a dict"

    result = ResultWithNonDictTimestamp()
    timestamps = _extract_timestamps_from_result(result)
    # Should return None since timestamp is not a dict
    assert timestamps is None


def test_extract_timestamps_from_result_dict_no_timestamp_key(tmp_path: Path) -> None:
    """Test _extract_timestamps_from_result with dict without timestamp key (line 137)."""
    result = {
        'text': 'some text',
        # No 'timestamp' key
    }
    timestamps = _extract_timestamps_from_result(result)
    assert timestamps is None

