"""Additional tests for ensure_mono_audio to achieve 100% coverage."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from inference_server.backend import BackendError
from inference_server.parakeet_utils import ensure_mono_audio


def test_ensure_mono_audio_stereo_to_mono(tmp_path: Path) -> None:
    """Test ensure_mono_audio converts stereo to mono (line 45-46)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_librosa = MagicMock()
    mock_soundfile = MagicMock()
    # Return stereo audio (2D array)
    stereo_audio = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])  # 2 channels
    mock_librosa.load.return_value = (stereo_audio, 44100)
    mock_librosa.to_mono.return_value = np.array([1.0, 2.0, 3.0])
    mock_librosa.resample.return_value = np.array([1.0, 2.0, 3.0])

    with patch.dict(sys.modules, {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = ensure_mono_audio(audio_path)
        assert isinstance(result, Path)
        # Should have called to_mono for stereo audio
        mock_librosa.to_mono.assert_called_once()


def test_ensure_mono_audio_resample(tmp_path: Path) -> None:
    """Test ensure_mono_audio resamples when sample rate is not 16kHz (lines 49-51)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_librosa = MagicMock()
    mock_soundfile = MagicMock()
    # Return audio with non-16kHz sample rate
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 22050)  # 22.05kHz
    mock_librosa.resample.return_value = np.array([1.0, 2.0, 3.0])

    with patch.dict(sys.modules, {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = ensure_mono_audio(audio_path)
        assert isinstance(result, Path)
        # Should have called resample
        mock_librosa.resample.assert_called_once()
        # Check that target_sr=16000 was passed (as keyword argument)
        assert mock_librosa.resample.call_args.kwargs.get("target_sr") == 16000


def test_ensure_mono_audio_no_resample_when_16khz(tmp_path: Path) -> None:
    """Test ensure_mono_audio doesn't resample when already 16kHz."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_librosa = MagicMock()
    mock_soundfile = MagicMock()
    # Return audio with 16kHz sample rate
    mock_librosa.load.return_value = (np.array([1.0, 2.0, 3.0]), 16000)  # Already 16kHz

    with patch.dict(sys.modules, {"librosa": mock_librosa, "soundfile": mock_soundfile}):
        result = ensure_mono_audio(audio_path)
        assert isinstance(result, Path)
        # Should NOT have called resample
        mock_librosa.resample.assert_not_called()


def test_ensure_mono_audio_librosa_exception_fallback(tmp_path: Path) -> None:
    """Test ensure_mono_audio falls back to ffmpeg when librosa raises exception (lines 63-65)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_librosa = MagicMock()
    mock_soundfile = MagicMock()
    # Make librosa.load raise exception
    mock_librosa.load.side_effect = RuntimeError("librosa failed")

    mock_subprocess = MagicMock()
    mock_subprocess.run.return_value = MagicMock(returncode=0)

    with patch.dict(sys.modules, {"librosa": mock_librosa, "soundfile": mock_soundfile}), \
         patch("inference_server.parakeet_utils.subprocess", mock_subprocess):
        result = ensure_mono_audio(audio_path)
        assert isinstance(result, Path)
        # Should have called ffmpeg via subprocess
        assert mock_subprocess.run.called


def test_ensure_mono_audio_ffmpeg_calledprocesserror(tmp_path: Path) -> None:
    """Test ensure_mono_audio handles ffmpeg CalledProcessError (lines 96-101)."""
    import subprocess

    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock librosa import to fail
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name in ("librosa", "soundfile"):
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    # Mock subprocess.run to raise CalledProcessError
    mock_subprocess_run = MagicMock()
    mock_error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"ffmpeg error")
    mock_subprocess_run.side_effect = mock_error

    with patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        with pytest.raises(BackendError) as exc_info:
            ensure_mono_audio(audio_path)
        assert "ffmpeg conversion failed" in str(exc_info.value)


def test_ensure_mono_audio_ffmpeg_calledprocesserror_bytes_stderr(tmp_path: Path) -> None:
    """Test ensure_mono_audio handles ffmpeg CalledProcessError with bytes stderr."""
    import subprocess

    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    # Mock librosa import to fail
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name in ("librosa", "soundfile"):
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    # Mock subprocess.run to raise CalledProcessError with bytes stderr
    mock_subprocess_run = MagicMock()
    mock_error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="ffmpeg error string")
    mock_subprocess_run.side_effect = mock_error

    with patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        with pytest.raises(BackendError) as exc_info:
            ensure_mono_audio(audio_path)
        assert "ffmpeg conversion failed" in str(exc_info.value)

