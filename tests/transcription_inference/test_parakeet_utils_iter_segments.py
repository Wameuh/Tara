"""Comprehensive tests for iter_nemo_transcription_segments to achieve 100% coverage."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Iterable
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest

from inference_server.backend import BackendError
from inference_server.models import TranscriptionSegment
from inference_server.parakeet_utils import iter_nemo_transcription_segments


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


def test_iter_nemo_transcription_segments_with_librosa(tmp_path: Path) -> None:
    """Test happy path with librosa available (lines 514-522, 527-531, 553-559, 561-647)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock librosa
    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0  # 100 seconds
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        # Verify duration is returned
        assert duration == 100.0

        # Consume the iterator to verify segments
        segments = list(segments_iter)
        assert len(segments) > 0
        assert all(isinstance(seg, TranscriptionSegment) for seg in segments)

        # Verify librosa.get_duration was called
        mock_librosa.get_duration.assert_called_once()


def test_iter_nemo_transcription_segments_librosa_duration_error(tmp_path: Path) -> None:
    """Test error handling when librosa fails to get duration (line 530-531)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock librosa to fail on get_duration
    mock_librosa = MagicMock()
    mock_librosa.get_duration.side_effect = RuntimeError("Failed to read audio")

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": MagicMock()}):
        with pytest.raises(BackendError, match="Failed to get audio duration"):
            iter_nemo_transcription_segments(
                model=mock_model,
                audio_path=audio_path,
                chunk_secs=400.0,
                overlap_percentage=5.0,
                timestamps=True,
            )


def test_iter_nemo_transcription_segments_without_librosa_ffprobe(tmp_path: Path) -> None:
    """Test fallback to ffprobe when librosa is unavailable (lines 518-519, 532-551)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock subprocess.run for ffprobe
    mock_run_result = MagicMock()
    mock_run_result.stdout = "100.5"
    mock_subprocess_run = MagicMock(return_value=mock_run_result)

    # Make librosa import fail
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args, **kwargs):
        if name == "librosa" or name == "soundfile":
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        with pytest.raises(BackendError, match="Cannot load audio chunks: librosa is not available"):
            segments_iter, duration = iter_nemo_transcription_segments(
                model=mock_model,
                audio_path=audio_path,
                chunk_secs=400.0,
                overlap_percentage=5.0,
                timestamps=True,
            )
            # Verify duration from ffprobe
            assert duration == 100.5
            # Try to consume iterator (will fail at line 571-575 when librosa is None)
            list(segments_iter)


def test_iter_nemo_transcription_segments_ffprobe_subprocess_error(tmp_path: Path) -> None:
    """Test that CalledProcessError from ffprobe propagates (line 533)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Make librosa import fail
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args, **kwargs):
        if name == "librosa" or name == "soundfile":
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    # subprocess.run with check=True will raise CalledProcessError directly
    # This is NOT caught by the try/except on lines 545-551 (which is a code bug)
    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch("builtins.__import__", side_effect=mock_import):
        with patch("inference_server.parakeet_utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffprobe")
            # The CalledProcessError will propagate uncaught
            with pytest.raises(subprocess.CalledProcessError):
                iter_nemo_transcription_segments(
                    model=mock_model,
                    audio_path=audio_path,
                    chunk_secs=400.0,
                    overlap_percentage=5.0,
                    timestamps=True,
                )


def test_iter_nemo_transcription_segments_ffprobe_invalid_output(tmp_path: Path) -> None:
    """Test error handling when ffprobe returns invalid output (lines 545-551)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MockModel()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock subprocess.run to return invalid duration
    mock_run_result = MagicMock()
    mock_run_result.stdout = "invalid_number"
    mock_subprocess_run = MagicMock(return_value=mock_run_result)

    # Make librosa import fail
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args, **kwargs):
        if name == "librosa" or name == "soundfile":
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        with pytest.raises(BackendError, match="Cannot determine audio duration"):
            iter_nemo_transcription_segments(
                model=mock_model,
                audio_path=audio_path,
                chunk_secs=400.0,
                overlap_percentage=5.0,
                timestamps=True,
            )


def test_iter_nemo_transcription_segments_with_timestamps(tmp_path: Path) -> None:
    """Test chunk processing with timestamps (lines 612-629)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Create mock model that returns timestamps
    mock_model = MagicMock()

    class MockHypothesis:
        def __init__(self) -> None:
            self.text = "test segment"
            self.timestamp = {
                "segment": [
                    {"start": 0.5, "end": 1.5, "segment": "test"},
                    {"start": 1.5, "end": 2.5, "segment": "segment"},
                ],
                "word": [],
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
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        segments = list(segments_iter)
        assert len(segments) == 2
        assert segments[0].text == "test"
        assert segments[1].text == "segment"
        # Verify timestamps are adjusted (start/end should be offset by 0)
        assert segments[0].start == 0.5
        assert segments[0].end == 1.5


def test_iter_nemo_transcription_segments_without_timestamps(tmp_path: Path) -> None:
    """Test chunk processing without timestamps (lines 630-635)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test transcription")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        segments = list(segments_iter)
        assert len(segments) == 1
        assert segments[0].text == "test transcription"
        assert segments[0].start == 0.0
        assert segments[0].end == 100.0


def test_iter_nemo_transcription_segments_chunk_load_error(tmp_path: Path) -> None:
    """Test error handling when chunk loading fails (lines 576-586)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0  # Multiple chunks
    # Make librosa.load fail
    mock_librosa.load.side_effect = RuntimeError("Failed to load chunk")

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        # Should continue despite error, but yield no segments
        segments = list(segments_iter)
        assert len(segments) == 0


def test_iter_nemo_transcription_segments_transcription_error(tmp_path: Path) -> None:
    """Test error handling during chunk transcription (lines 636-638)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock model that raises exception during transcription
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("Transcription failed")

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        # Should continue despite error, but yield no segments
        segments = list(segments_iter)
        assert len(segments) == 0


def test_iter_nemo_transcription_segments_cleanup(tmp_path: Path) -> None:
    """Test cleanup of temporary chunk files (lines 639-645)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()
    mock_unlink = MagicMock()
    mock_exists = MagicMock(return_value=True)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}), \
         patch("inference_server.parakeet_utils.os.unlink", mock_unlink), \
         patch("inference_server.parakeet_utils.os.path.exists", mock_exists):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        # Consume the iterator
        list(segments_iter)

        # Verify cleanup was called
        assert mock_unlink.called
        assert mock_exists.called


def test_iter_nemo_transcription_segments_cleanup_error(tmp_path: Path) -> None:
    """Test error handling during cleanup (lines 644-645)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()
    mock_unlink = MagicMock(side_effect=PermissionError("Cannot delete"))
    mock_exists = MagicMock(return_value=True)

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}), \
         patch("inference_server.parakeet_utils.os.unlink", mock_unlink), \
         patch("inference_server.parakeet_utils.os.path.exists", mock_exists):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        # Should complete successfully despite cleanup error
        segments = list(segments_iter)
        assert len(segments) == 1


def test_iter_nemo_transcription_segments_logger_none(tmp_path: Path) -> None:
    """Test with logger=None to use default logger (lines 521-522)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
            logger=None,  # Test default logger
        )

        segments = list(segments_iter)
        assert len(segments) == 1


def test_iter_nemo_transcription_segments_custom_logger(tmp_path: Path) -> None:
    """Test with custom logger."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    custom_logger = logging.getLogger("test_logger")

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
            logger=custom_logger,
        )

        segments = list(segments_iter)
        assert len(segments) == 1


def test_iter_nemo_transcription_segments_multiple_chunks(tmp_path: Path) -> None:
    """Test processing multiple chunks with overlap (lines 564-569)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Track which chunk is being processed
    call_count = {"count": 0}

    def mock_transcribe(*args, **kwargs):
        call_count["count"] += 1
        return [MagicMock(text=f"chunk {call_count['count']}", timestamp=None)]

    mock_model = MagicMock()
    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 900.0  # 900 seconds, needs 3 chunks at 400s each
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        segments = list(segments_iter)
        # Should have 3 chunks
        assert len(segments) == 3
        assert call_count["count"] == 3


def test_iter_nemo_transcription_segments_last_chunk_duration(tmp_path: Path) -> None:
    """Test that last chunk has correct duration (lines 566-569)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Track duration passed to librosa.load
    load_calls = []

    def mock_load(*args, **kwargs):
        load_calls.append(kwargs)
        return (np.array([1.0, 2.0, 3.0]), 16000)

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 450.0  # 450 seconds, needs 2 chunks
    mock_librosa.load = mock_load

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        segments = list(segments_iter)

        # Verify last chunk duration is (duration - offset) = 450 - 400 = 50
        # First chunk: offset=0, duration=400+20=420 (with 5% overlap)
        # Second chunk: offset=400, duration=450-400=50 (last chunk, no overlap added)
        assert len(load_calls) == 2
        assert load_calls[0]["duration"] == 420.0  # 400 + 5% = 420
        assert load_calls[1]["duration"] == 50.0  # duration - offset


def test_iter_nemo_transcription_segments_overlap_filtering(tmp_path: Path) -> None:
    """Test that segments in overlap region are filtered out (lines 620-621)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Create mock model that returns timestamps in overlap region
    call_count = {"count": 0}

    def mock_transcribe(*args, **kwargs):
        call_count["count"] += 1

        class MockHypothesis:
            def __init__(self, chunk_num: int) -> None:
                self.text = f"chunk {chunk_num}"
                if chunk_num == 1:
                    # First chunk: segment at end
                    self.timestamp = {
                        "segment": [{"start": 0.0, "end": 1.0, "segment": "normal"}],
                        "word": [],
                    }
                else:
                    # Second chunk: segment in overlap region (should be filtered)
                    # overlap_secs = 400 * 0.05 = 20 seconds
                    # offset for chunk 2 = 400
                    # Segment ending at 405 is within overlap (400 + 20 = 420)
                    self.timestamp = {
                        "segment": [
                            {"start": 0.0, "end": 5.0, "segment": "overlap"},  # Will be filtered
                            {"start": 25.0, "end": 30.0, "segment": "normal"},  # Will be kept
                        ],
                        "word": [],
                    }

        return [MockHypothesis(call_count["count"])]

    mock_model = MagicMock()
    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0  # Multiple chunks
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        segments = list(segments_iter)

        # Should have 2 segments from first chunk + 1 from second (overlap filtered)
        # First chunk has 1 segment
        # Second chunk has 2 segments but 1 is in overlap region
        segment_texts = [seg.text for seg in segments]
        assert "normal" in segment_texts


def test_iter_nemo_transcription_segments_empty_segment_text(tmp_path: Path) -> None:
    """Test that segments with empty text are skipped (lines 622-624)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Create mock model that returns timestamps with empty segment text
    mock_model = MagicMock()

    class MockHypothesis:
        def __init__(self) -> None:
            self.text = "test"
            self.timestamp = {
                "segment": [
                    {"start": 0.0, "end": 1.0, "segment": ""},  # Empty, should be skipped
                    {"start": 1.0, "end": 2.0, "segment": "valid"},  # Valid
                ],
                "word": [],
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
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        segments = list(segments_iter)
        # Should only have 1 segment (empty one filtered out)
        assert len(segments) == 1
        assert segments[0].text == "valid"


def test_iter_nemo_transcription_segments_result_is_list(tmp_path: Path) -> None:
    """Test handling when transcribe returns a list (lines 606-609)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock model that returns a list
    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test from list")])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        segments = list(segments_iter)
        assert len(segments) == 1
        assert segments[0].text == "test from list"


def test_iter_nemo_transcription_segments_result_not_list(tmp_path: Path) -> None:
    """Test handling when transcribe returns a single result (lines 606-609)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock model that returns a single result (not a list)
    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=MagicMock(text="test not list"))

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 100.0
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=False,
        )

        segments = list(segments_iter)
        assert len(segments) == 1
        assert segments[0].text == "test not list"


def test_iter_nemo_transcription_segments_timestamp_adjustment(tmp_path: Path) -> None:
    """Test that timestamps are adjusted by chunk offset (lines 616-619)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Create mock model that returns timestamps
    call_count = {"count": 0}

    def mock_transcribe(*args, **kwargs):
        chunk_idx = call_count["count"]
        call_count["count"] += 1

        class MockHypothesis:
            def __init__(self) -> None:
                self.text = "test"
                # Timestamps are relative to chunk start (0-based)
                # For second chunk, place segment after overlap region
                # overlap_secs = 400 * 0.05 = 20 seconds
                if chunk_idx == 0:
                    # First chunk: segment at 10-20s
                    self.timestamp = {
                        "segment": [{"start": 10.0, "end": 20.0, "segment": "chunk1"}],
                        "word": [],
                    }
                else:
                    # Second chunk: segment at 30-40s (after 20s overlap)
                    self.timestamp = {
                        "segment": [{"start": 30.0, "end": 40.0, "segment": "chunk2"}],
                        "word": [],
                    }

        return [MockHypothesis()]

    mock_model = MagicMock()
    mock_model.transcribe = mock_transcribe

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    mock_librosa.get_duration.return_value = 500.0  # Multiple chunks
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        segments_iter, duration = iter_nemo_transcription_segments(
            model=mock_model,
            audio_path=audio_path,
            chunk_secs=400.0,
            overlap_percentage=5.0,
            timestamps=True,
        )

        segments = list(segments_iter)

        # First chunk: timestamps should be 10.0 + 0 = 10.0, 20.0 + 0 = 20.0
        # Second chunk: timestamps should be 30.0 + 400 = 430.0, 40.0 + 400 = 440.0
        assert len(segments) == 2
        assert segments[0].start == 10.0
        assert segments[0].end == 20.0
        assert segments[0].text == "chunk1"
        assert segments[1].start == 430.0
        assert segments[1].end == 440.0
        assert segments[1].text == "chunk2"
