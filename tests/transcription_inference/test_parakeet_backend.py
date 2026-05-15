"""Tests for ParakeetBackend."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from inference_server.backend import BackendError
from inference_server.models import TranscriptionResponse, TranscriptionSegment
from inference_server.parakeet_backend import ParakeetBackend


class _DummyNeMoModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def transcribe(self, audio_paths: list[str], *, return_hypotheses: bool = False, timestamps: bool = True):
        self.calls.append(audio_paths)
        # Return a simple result with text and timestamps
        class Hypothesis:
            def __init__(self, text: str):
                self.text = text
                if timestamps:
                    self.timestamp = {
                        'segment': [
                            {'start': 0.0, 'end': 1.0, 'segment': text[:20] if len(text) > 20 else text}
                        ],
                        'word': [],
                        'char': []
                    }

        return [Hypothesis("test transcription")]


def _install_dummy_nemo(monkeypatch: pytest.MonkeyPatch, model: _DummyNeMoModel) -> None:
    module = ModuleType("nemo.collections.asr.models")
    module.ASRModel = type("ASRModel", (), {
        "from_pretrained": classmethod(lambda cls, model_name: model)
    })
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.models", module)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", MagicMock())
    monkeypatch.setitem(sys.modules, "nemo", MagicMock())


def test_parakeet_backend_transcribes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    # Mock ensure_mono_audio to return the input path
    # Patch where it's used (parakeet_backend) since it's imported there
    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mock_ensure_mono.return_value = tmp_path / "mono.wav"
        mock_transcribe.return_value = {
            'text': 'test transcription',
            'timestamps': None,
            'segment_timestamps': [
                {'start': 0.0, 'end': 1.0, 'segment': 'test transcription'}
            ],
            'duration': 1.0
        }

        backend = ParakeetBackend()
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        result = backend.transcribe(audio, model="parakeet:test-model", language="en")

        assert result.text == "test transcription"
        assert result.model == "parakeet:test-model"
        assert len(result.segments) == 1
        assert result.segments[0].text == "test transcription"
        mock_ensure_mono.assert_called_once()


def test_parakeet_backend_missing_dependency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original_import = __import__

    def fake_import(name: str, *args, **kwargs):
        if name == "nemo" or name.startswith("nemo."):
            raise ImportError("No module named 'nemo'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "nemo", None)
    monkeypatch.setattr("builtins.__import__", fake_import)
    backend = ParakeetBackend()
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"\x00\x00")

    with pytest.raises(BackendError, match="Install 'nemo"):
        backend.transcribe(audio, model="parakeet:test-model", language=None)


def test_parakeet_backend_model_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    backend = ParakeetBackend()
    first = backend._load_model("test-model")
    second = backend._load_model("test-model")

    assert first is second


def test_parakeet_backend_release_all(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    backend = ParakeetBackend()
    backend._models["x"] = dummy_model  # type: ignore[assignment]

    backend.release_all()

    assert backend._models == {}


def test_parakeet_backend_extracts_model_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mock_ensure_mono.return_value = tmp_path / "mono.wav"
        mock_transcribe.return_value = {
            'text': 'test',
            'timestamps': None,
            'segment_timestamps': [],
            'duration': 1.0
        }

        backend = ParakeetBackend()
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        backend.transcribe(audio, model="parakeet:nvidia/parakeet-tdt-0.6b-v3", language=None)

        # Should have called _load_model with the model name without prefix
        assert "nvidia/parakeet-tdt-0.6b-v3" in backend._models


def test_parakeet_backend_stream_transcribe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    with patch("inference_server.parakeet_backend.iter_nemo_transcription_segments") as mock_iter_segments:
        # Mock iter_nemo_transcription_segments to return segments and duration
        test_segment = TranscriptionSegment(start=0.0, end=1.0, text="test transcription")
        mock_iter_segments.return_value = (iter([test_segment]), 1.0)

        backend = ParakeetBackend()
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        segments, info = backend.stream_transcribe(audio, model="parakeet:test-model", language="en")
        collected = list(segments)

        assert len(collected) == 1
        assert hasattr(collected[0], 'text')
        assert collected[0].text == "test transcription"
        assert hasattr(info, 'duration')
        assert info.duration == 1.0


def test_parakeet_backend_audio_preprocessing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that audio preprocessing (mono conversion) is called."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mono_path = tmp_path / "mono.wav"
        mock_ensure_mono.return_value = mono_path
        mock_transcribe.return_value = {
            'text': 'test',
            'timestamps': None,
            'segment_timestamps': [],
            'duration': 1.0
        }

        backend = ParakeetBackend()
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        backend.transcribe(audio, model="parakeet:test-model", language=None)

        # Should call ensure_mono_audio with the audio path
        mock_ensure_mono.assert_called_once_with(audio)
        # Should call transcribe_with_nemo_partial_audio with the mono path
        assert mock_transcribe.call_args[1]['audio_path'] == mono_path


def test_parakeet_backend_cleanup_temp_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that temporary mono audio file is cleaned up."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    mono_path = tmp_path / "mono_temp.wav"
    mono_path.write_bytes(b"temp")

    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mock_ensure_mono.return_value = mono_path
        mock_transcribe.return_value = {
            'text': 'test',
            'timestamps': None,
            'segment_timestamps': [],
            'duration': 1.0
        }

        backend = ParakeetBackend()
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        backend.transcribe(audio, model="parakeet:test-model", language=None)

        # Temp file should be deleted
        assert not mono_path.exists()


def test_parakeet_backend_timestamps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that timestamps are extracted and formatted correctly."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mock_ensure_mono.return_value = tmp_path / "mono.wav"
        mock_transcribe.return_value = {
            'text': 'first second third',
            'timestamps': None,
            'segment_timestamps': [
                {'start': 0.0, 'end': 0.5, 'segment': 'first'},
                {'start': 0.5, 'end': 1.0, 'segment': 'second'},
                {'start': 1.0, 'end': 1.5, 'segment': 'third'},
            ],
            'duration': 1.5
        }

        backend = ParakeetBackend()
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        result = backend.transcribe(audio, model="parakeet:test-model", language=None)

        assert len(result.segments) == 3
        assert result.segments[0].start == 0.0
        assert result.segments[0].end == 0.5
        assert result.segments[0].text == "first"
        assert result.segments[2].start == 1.0
        assert result.segments[2].end == 1.5


def test_parakeet_backend_chunking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that large files are chunked correctly."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mock_ensure_mono.return_value = tmp_path / "mono.wav"
        mock_transcribe.return_value = {
            'text': 'test',
            'timestamps': None,
            'segment_timestamps': [],
            'duration': 500.0  # 500 seconds
        }

        backend = ParakeetBackend(chunk_secs=100.0)  # 100 second chunks
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        backend.transcribe(audio, model="parakeet:test-model", language=None)

        # Check that transcribe_with_nemo_partial_audio was called with chunk_secs
        assert mock_transcribe.call_args[1]['chunk_secs'] == 100.0


def test_parakeet_backend_overlap_merging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that overlapping chunks are merged correctly."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)

    with patch("inference_server.parakeet_backend.ensure_mono_audio") as mock_ensure_mono, \
         patch("inference_server.parakeet_backend.transcribe_with_nemo_partial_audio") as mock_transcribe:
        mock_ensure_mono.return_value = tmp_path / "mono.wav"
        mock_transcribe.return_value = {
            'text': 'merged transcription',
            'timestamps': None,
            'segment_timestamps': [],
            'duration': 1.0
        }

        backend = ParakeetBackend(overlap_percentage=10.0)
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"\x00\x00")

        backend.transcribe(audio, model="parakeet:test-model", language=None)

        # Check that transcribe_with_nemo_partial_audio was called with overlap_percentage
        assert mock_transcribe.call_args[1]['overlap_percentage'] == 10.0


def test_parakeet_backend_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that configuration can be overridden via environment variables."""
    monkeypatch.setenv("PARAKEET_CHUNK_SIZE", "200.0")
    monkeypatch.setenv("PARAKEET_OVERLAP_PERCENTAGE", "10.0")

    backend = ParakeetBackend()

    assert backend._chunk_secs == 200.0
    assert backend._overlap_percentage == 10.0


def test_parakeet_backend_invalid_env_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that invalid environment variables fall back to defaults."""
    monkeypatch.setenv("PARAKEET_CHUNK_SIZE", "invalid")
    monkeypatch.setenv("PARAKEET_OVERLAP_PERCENTAGE", "not-a-number")

    backend = ParakeetBackend()

    # Should use defaults when env vars are invalid
    assert backend._chunk_secs == 400.0
    assert backend._overlap_percentage == 5.0






