"""Tests for ffprobe error handling in transcribe_with_nemo_partial_audio."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from inference_server.backend import BackendError
from inference_server.parakeet_utils import transcribe_with_nemo_partial_audio


def test_transcribe_with_nemo_partial_audio_ffprobe_valueerror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcribe_with_nemo_partial_audio handles ffprobe ValueError (lines 323-324)."""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_mono_path = tmp_path / "mono.wav"
    mock_mono_path.write_bytes(b"mono audio")

    # Mock librosa to be None (not available)
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name in ("librosa", "soundfile"):
            raise ImportError(f"No module named '{name}'")
        return original_import(name, *args, **kwargs)

    # Mock subprocess.run for ffprobe to return invalid output
    mock_subprocess_run = MagicMock()
    mock_subprocess_run.return_value = MagicMock(stdout="invalid\n", returncode=0)

    with patch("builtins.__import__", side_effect=mock_import), \
         patch("inference_server.parakeet_utils.ensure_mono_audio", return_value=mock_mono_path), \
         patch("inference_server.parakeet_utils.subprocess.run", mock_subprocess_run):
        with pytest.raises(BackendError) as exc_info:
            transcribe_with_nemo_partial_audio(
                model=mock_model,
                audio_path=audio_path,
                chunk_secs=400.0,
                overlap_percentage=0.0,
                timestamps=False,
            )
        # Should raise BackendError when float() conversion fails
        assert "Cannot determine audio duration" in str(exc_info.value) or "ffprobe" in str(exc_info.value)


# Note: The CalledProcessError exception handler at line 323 is actually unreachable
# because subprocess.run is called with check=True, which raises CalledProcessError
# before the try block. The exception handler can only catch ValueError from float().
# This appears to be a code structure issue, but we'll test the ValueError path instead.

