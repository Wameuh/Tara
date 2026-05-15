"""Tests for parakeet_utils functions."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from inference_server.backend import BackendError
from inference_server.parakeet_utils import (
    _extract_text_from_result,
    _extract_timestamps_from_result,
    _find_overlap_start,
    _merge_overlapping_transcriptions,
    ensure_mono_audio,
)


def test_extract_text_from_result_string() -> None:
    """Extract text from string result."""
    result = "test transcription"
    assert _extract_text_from_result(result) == "test transcription"


def test_extract_text_from_result_hypothesis() -> None:
    """Extract text from Hypothesis object."""
    class Hypothesis:
        def __init__(self):
            self.text = "hypothesis text"

    result = Hypothesis()
    assert _extract_text_from_result(result) == "hypothesis text"


def test_extract_text_from_result_other() -> None:
    """Extract text from other object (converts to string)."""
    result = 123
    assert _extract_text_from_result(result) == "123"


def test_extract_timestamps_from_result_with_attr() -> None:
    """Extract timestamps from result with timestamp attribute."""
    class Result:
        def __init__(self):
            self.timestamp = {
                'segment': [{'start': 0.0, 'end': 1.0, 'segment': 'test'}],
                'word': []
            }

    result = Result()
    timestamps = _extract_timestamps_from_result(result)
    assert timestamps is not None
    assert 'segment' in timestamps
    assert len(timestamps['segment']) == 1


def test_extract_timestamps_from_result_dict() -> None:
    """Extract timestamps from dict-like result."""
    result = {
        'timestamp': {
            'segment': [{'start': 0.0, 'end': 1.0}]
        }
    }
    timestamps = _extract_timestamps_from_result(result)
    assert timestamps is not None
    assert 'segment' in timestamps


def test_extract_timestamps_from_result_none() -> None:
    """Return None when no timestamps available."""
    result = "just text"
    assert _extract_timestamps_from_result(result) is None


def test_find_overlap_start_exact_match() -> None:
    """Find overlap when words match exactly."""
    text1 = "the quick brown fox jumps"
    text2 = "brown fox jumps over"
    overlap = _find_overlap_start(text1, text2, min_overlap_words=2)
    assert overlap == 3  # "brown fox jumps"


def test_find_overlap_start_case_insensitive() -> None:
    """Find overlap with case-insensitive matching."""
    text1 = "The Quick Brown"
    text2 = "quick brown fox"
    overlap = _find_overlap_start(text1, text2, min_overlap_words=2, tolerance=0.8)
    assert overlap >= 2


def test_find_overlap_start_no_overlap() -> None:
    """Return 0 when no overlap found."""
    text1 = "completely different text"
    text2 = "another set of words"
    overlap = _find_overlap_start(text1, text2)
    assert overlap == 0


def test_merge_overlapping_transcriptions_no_overlap() -> None:
    """Merge transcriptions without overlap."""
    transcriptions = ["first chunk", "second chunk", "third chunk"]
    merged = _merge_overlapping_transcriptions(transcriptions)
    assert merged == "first chunk second chunk third chunk"


def test_merge_overlapping_transcriptions_with_overlap() -> None:
    """Merge transcriptions with overlapping content."""
    transcriptions = [
        "the quick brown fox jumps",
        "brown fox jumps over the lazy dog"
    ]
    merged = _merge_overlapping_transcriptions(transcriptions)
    # Should remove duplicate "brown fox jumps"
    assert "brown fox jumps" in merged
    assert merged.count("brown fox jumps") == 1  # Should appear only once


def test_merge_overlapping_transcriptions_empty() -> None:
    """Handle empty transcription list."""
    assert _merge_overlapping_transcriptions([]) == ""


def test_merge_overlapping_transcriptions_single() -> None:
    """Handle single transcription."""
    assert _merge_overlapping_transcriptions(["single"]) == "single"


def test_ensure_mono_audio_with_librosa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test ensure_mono_audio with librosa."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock librosa and soundfile
    mock_librosa = MagicMock()
    mock_soundfile = MagicMock()
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 44100)  # mono, 44.1kHz
    mock_librosa.to_mono.return_value = np.array([1.0, 2.0, 3.0])
    mock_librosa.resample.return_value = np.array([1.0, 2.0, 3.0])

    with patch.dict(sys.modules, {'librosa': mock_librosa, 'soundfile': mock_soundfile}):
        result = ensure_mono_audio(audio_path)
        assert isinstance(result, Path)
        # Should have called librosa.load
        mock_librosa.load.assert_called_once()


def test_ensure_mono_audio_fallback_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test ensure_mono_audio falls back to ffmpeg when librosa unavailable."""
    import builtins

    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock subprocess.run for ffmpeg
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(returncode=0)

    # Make librosa import fail by patching __import__
    original_import = builtins.__import__

    def mock_import(name: str, *args, **kwargs):
        if name == "librosa" or name == "soundfile":
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_run):
        result = ensure_mono_audio(audio_path)
        # Should have called ffmpeg via subprocess.run
        assert mock_run.called
        # Verify ffmpeg command was called
        call_args = mock_run.call_args[0][0]
        assert "ffmpeg" in call_args[0] or any("ffmpeg" in str(arg) for arg in call_args)
        assert isinstance(result, Path)


def test_ensure_mono_audio_no_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test ensure_mono_audio raises BackendError when no library available."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock both librosa and ffmpeg as unavailable
    def fake_import(name: str, *args, **kwargs):
        if name in ("librosa", "soundfile"):
            raise ImportError("not available")
        return __import__(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import), \
         patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(BackendError, match="No audio conversion library found"):
            ensure_mono_audio(audio_path)






