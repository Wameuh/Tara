"""Additional tests for transcribe_with_nemo_partial_audio to achieve 100% coverage."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest

from inference_server.backend import BackendError
from inference_server.parakeet_utils import transcribe_with_nemo_partial_audio


class MockModel:
    """Mock NeMo model for testing."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def transcribe(
        self,
        audio_paths: list[str],
        *,
        return_hypotheses: bool = False,
        timestamps: bool = True,
    ) -> list[object]:
        """Mock transcribe method."""
        self.calls.append(audio_paths)

        class Hypothesis:
            def __init__(self, text: str, start: float = 0.0, end: float = 1.0) -> None:
                self.text = text
                if timestamps:
                    self.timestamp = {
                        "segment": [{"start": start, "end": end, "segment": text}],
                        "word": [],
                        "char": [],
                    }

        return [Hypothesis("test transcription")]


def test_transcribe_with_nemo_partial_audio_single_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio with single chunk (no chunking needed)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()

    # Mock ensure_mono_audio
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock librosa for duration
    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0  # 100 seconds, less than chunk_secs

    # Mock subprocess for chunk extraction (won't be called for single chunk, but needed for setup)
    mock_subprocess_run = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
        )

        assert "text" in result
        assert "duration" in result
        assert result["duration"] == 100.0


def test_transcribe_with_nemo_partial_audio_multiple_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio with multiple chunks."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="chunk text", timestamp={"segment": []})])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock librosa for duration (long audio requiring chunking)
    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 1000.0  # 1000 seconds, needs chunking

    # Mock subprocess.run for ffmpeg chunk extraction
    mock_subprocess_run = MagicMock()
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run), \
         patch("inference_server.parakeet_utils.os.path.exists", return_value=True), \
         patch("inference_server.parakeet_utils.os.unlink", MagicMock()):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,  # 400 second chunks
            overlap_percentage=5.0,
            timestamps=True,
        )

        assert "text" in result
        assert "duration" in result


def test_transcribe_with_nemo_partial_audio_chunk_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio handles chunk processing exceptions (lines 442-446)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()

    # Make transcribe raise an exception for one chunk
    call_count = {"count": 0}

    def mock_transcribe(*args: object, **kwargs: object) -> list[object]:
        call_count["count"] += 1
        if call_count["count"] == 2:  # Fail on second chunk
            raise RuntimeError("Chunk processing failed")
        return [MagicMock(text="chunk text", timestamp={"segment": []})]

    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 1000.0  # Requires chunking

    mock_subprocess_run = MagicMock()
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run), \
         patch("inference_server.parakeet_utils.os.path.exists", return_value=True), \
         patch("inference_server.parakeet_utils.os.unlink", MagicMock()):
        # Should continue processing other chunks despite exception
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
        )

        assert "text" in result


def test_transcribe_with_nemo_partial_audio_cleanup_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio handles cleanup exceptions (lines 454-455)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test", timestamp={"segment": []})])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0

    # Mock os.unlink to raise exception
    mock_unlink = MagicMock(side_effect=PermissionError("Cannot delete"))

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}), \
         patch("inference_server.parakeet_utils.os.unlink", mock_unlink):
        # Should complete successfully despite cleanup exception
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
        )

        assert "text" in result


def test_transcribe_with_nemo_partial_audio_merge_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio merges overlapping chunks (lines 460-470)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()

    # Return different texts for each chunk to test merging
    call_count = {"count": 0}
    texts = ["first chunk", "first chunk second chunk"]  # Overlapping

    def mock_transcribe(*args: object, **kwargs: object) -> list[object]:
        text = texts[call_count["count"]]
        call_count["count"] += 1
        return [MagicMock(text=text, timestamp={"segment": []})]

    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0  # Requires chunking

    mock_subprocess_run = MagicMock()
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run), \
         patch("inference_server.parakeet_utils.os.path.exists", return_value=True), \
         patch("inference_server.parakeet_utils.os.unlink", MagicMock()):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,  # With overlap
            timestamps=True,
        )

        assert "text" in result
        # Text should be merged
        assert isinstance(result["text"], str)


def test_transcribe_with_nemo_partial_audio_no_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio with no overlap (line 472-473)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="chunk text", timestamp={"segment": []})])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0

    mock_subprocess_run = MagicMock()
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run), \
         patch("inference_server.parakeet_utils.os.path.exists", return_value=True), \
         patch("inference_server.parakeet_utils.os.unlink", MagicMock()):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,  # No overlap
            timestamps=True,
        )

        assert "text" in result


def test_transcribe_with_nemo_partial_audio_combine_timestamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio combines timestamps (lines 478-494)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()

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
    # Mock librosa.load to return proper tuple (audio_data, sample_rate)
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,  # Enable timestamps
        )

        assert "timestamps" in result
        assert result["timestamps"] is not None
        assert "segment" in result["timestamps"]
        assert "word" in result["timestamps"]


def test_transcribe_with_nemo_partial_audio_no_timestamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio without timestamps."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}):
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=False,  # No timestamps
        )

        assert "text" in result
        assert result.get("timestamps") is None or result["timestamps"] is None


def test_transcribe_with_nemo_partial_audio_logger_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio uses default logger when None (line 291)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test", timestamp={"segment": []})])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}):
        # Pass logger=None to test default logger usage
        result = transcribe_with_nemo_partial_audio(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=0.0,
            timestamps=True,
            logger=None,  # Should use default logger
        )

        assert "text" in result

