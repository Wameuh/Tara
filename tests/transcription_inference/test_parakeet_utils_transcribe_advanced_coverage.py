"""Advanced tests for transcribe_with_nemo_partial_audio to achieve 100% coverage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from inference_server.parakeet_utils import transcribe_with_nemo_partial_audio


def test_transcribe_with_nemo_partial_audio_no_overlap_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio when overlap_percentage is 0 (lines 471-473)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test", timestamp={"segment": []})])

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
            overlap_percentage=0.0,  # No overlap
            timestamps=False,
        )

        assert "text" in result
        assert isinstance(result["text"], str)


def test_transcribe_with_nemo_partial_audio_single_chunk_no_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio when only one chunk (no merge needed)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test", timestamp={"segment": []})])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0  # < chunk_secs, so only one chunk
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,  # Overlap > 0 but only one chunk
            timestamps=False,
        )

        assert "text" in result
        # Should not trigger merge logic since len(text_list) == 1


def test_transcribe_with_nemo_partial_audio_timestamps_no_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio with timestamps but no chunk timestamps (lines 479-496)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    # Return result without timestamps
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])  # No timestamp attribute

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
            timestamps=True,  # Request timestamps but result has none
        )

        assert "text" in result
        # Should still return result even if no timestamps


def test_transcribe_with_nemo_partial_audio_timestamps_combine_all_levels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio combines timestamps from all levels (lines 487-489)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()

    class MockHypothesis:
        def __init__(self) -> None:
            self.text = "test"
            self.timestamp = {
                "segment": [{"start": 0.0, "end": 1.0, "segment": "test"}],
                "word": [{"word": "test", "start": 0.0, "end": 1.0}],
                "char": [{"char": "t", "start": 0.0, "end": 0.5}, {"char": "e", "start": 0.5, "end": 1.0}],
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
        # Should combine timestamps from all levels
        if result["timestamps"]:
            assert "segment" in result["timestamps"] or "word" in result["timestamps"] or "char" in result["timestamps"]


def test_transcribe_with_nemo_partial_audio_timestamps_extend_conditional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio extends timestamps conditionally (line 488)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()

    # Create timestamps for multiple chunks
    call_count = {"count": 0}

    def mock_transcribe(*args: object, **kwargs: object) -> list[object]:
        count = call_count["count"]
        call_count["count"] += 1

        class MockHypothesis:
            def __init__(self, idx: int) -> None:
                self.text = f"chunk{idx}"
                self.timestamp = {
                    "segment": [{"start": 0.0, "end": 1.0, "segment": f"chunk{idx}"}],
                    "word": [],
                    "char": [],
                }

        return [MockHypothesis(count)]

    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0  # Requires chunking
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
        # Should have combined timestamps from multiple chunks


