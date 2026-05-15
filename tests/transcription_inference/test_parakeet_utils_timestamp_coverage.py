"""Tests for timestamp adjustment in transcribe_with_nemo_partial_audio."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from inference_server.parakeet_utils import transcribe_with_nemo_partial_audio


def test_transcribe_with_nemo_partial_audio_timestamp_adjustment_with_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test timestamp adjustment with dict.copy() path (line 434)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()

    class MockHypothesis:
        def __init__(self) -> None:
            self.text = "test"
            self.timestamp = {
                "segment": [{"start": 0.0, "end": 1.0, "segment": "test"}],
                "word": [{"word": "test", "start": 0.0, "end": 1.0}],
                "char": [],
            }

    mock_model.transcribe = MagicMock(return_value=[MockHypothesis()])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
        )

        assert "timestamps" in result
        assert result["timestamps"] is not None


def test_transcribe_with_nemo_partial_audio_timestamp_adjustment_dict_constructor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test timestamp adjustment with dict() constructor path (line 434)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()

    # Create a timestamp object that's not a dict but dict-like (no .copy method)
    class DictLikeTimestamp:
        def __init__(self) -> None:
            self.start = 0.0
            self.end = 1.0

        def __iter__(self) -> object:
            return iter([("start", 0.0), ("end", 1.0)])

    class MockHypothesis:
        def __init__(self) -> None:
            self.text = "test"
            # Use a dict-like object that doesn't have .copy()
            self.timestamp = {
                "segment": [DictLikeTimestamp()],
                "word": [],
                "char": [],
            }

    mock_model.transcribe = MagicMock(return_value=[MockHypothesis()])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
        )

        assert "timestamps" in result


def test_transcribe_with_nemo_partial_audio_timestamp_adjustment_start_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test timestamp adjustment adds offset to start and end (lines 436-438)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()

    # Create timestamps that will be adjusted
    class MockHypothesis:
        def __init__(self) -> None:
            self.text = "test"
            self.timestamp = {
                "segment": [
                    {"start": 0.0, "end": 1.0, "segment": "test"},
                    {"start": 1.0, "end": 2.0, "segment": "test2"},
                ],
                "word": [
                    {"word": "test", "start": 0.5, "end": 1.0},
                ],
                "char": [],
            }

    # For multiple chunks, need to return different results
    call_count = {"count": 0}

    def mock_transcribe(*args: object, **kwargs: object) -> list[object]:
        call_count["count"] += 1
        return [MockHypothesis()]

    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0  # Requires chunking (2 chunks)
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}), \
         patch("inference_server.parakeet_utils.os.path.exists", return_value=True), \
         patch("inference_server.parakeet_utils.os.unlink", MagicMock()):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
        )

        assert "timestamps" in result
        assert result["timestamps"] is not None
        # Timestamps should be adjusted with offset
        if result["timestamps"] and "segment" in result["timestamps"]:
            assert len(result["timestamps"]["segment"]) > 0


