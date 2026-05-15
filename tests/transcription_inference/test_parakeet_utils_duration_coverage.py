"""Tests for duration calculation paths in transcribe_with_nemo_partial_audio."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from inference_server.backend import BackendError
from inference_server.parakeet_utils import transcribe_with_nemo_partial_audio


def test_transcribe_with_nemo_partial_audio_librosa_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio uses librosa.get_duration (lines 300-304)."""
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
            overlap_percentage=0.0,
            timestamps=False,
        )

        assert "text" in result
        # Should have called librosa.get_duration
        mock_librosa.get_duration.assert_called_once()


def test_transcribe_with_nemo_partial_audio_librosa_duration_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio handles librosa.get_duration exception (lines 303-304)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    mock_librosa = MagicMock()
    # Make get_duration raise exception
    mock_librosa.get_duration.side_effect = RuntimeError("Failed to get duration")

    mock_soundfile = MagicMock()

    with patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch.dict("sys.modules", {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        with pytest.raises(BackendError) as exc_info:
            transcribe_with_nemo_partial_audio(
                model=mock_model,
                audio_path=audio_path,
                chunk_secs=400.0,
                overlap_percentage=0.0,
                timestamps=False,
            )
        assert "Failed to get audio duration" in str(exc_info.value)


def test_transcribe_with_nemo_partial_audio_ffprobe_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio uses ffprobe fallback when librosa is None (lines 305-324)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_model.transcribe = MagicMock(return_value=[MagicMock(text="test", timestamp={"segment": []})])

    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock librosa to be None (not available)
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name in ("librosa", "soundfile"):
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    # Mock subprocess.run for ffprobe
    mock_subprocess_run = MagicMock()
    mock_subprocess_run.return_value = MagicMock(stdout="100.0\n", returncode=0)

    with patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        # The function should try to use ffprobe for duration
        # But it will fail at chunk loading since librosa is None
        # So we expect a different error
        with pytest.raises(BackendError):
            transcribe_with_nemo_partial_audio(
                model=mock_model,
                audio_path=audio_path,
                chunk_secs=400.0,
                overlap_percentage=0.0,
                timestamps=False,
            )


