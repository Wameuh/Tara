"""Tests for ParakeetBackend."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from inference_server.backend import BackendError
from inference_server.models import TranscriptionSegment
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


def test_parakeet_backend_uses_persistent_nemo_extract_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cached .nemo archives are extracted outside NeMo's temporary directory."""
    monkeypatch.setenv("TARA_NEMO_EXTRACT_DIR", str(tmp_path / "cache"))
    model_path = tmp_path / "model.nemo"
    model_path.write_bytes(b"fixture")
    unpack_calls: list[tuple[str, str]] = []

    class Connector:
        def _unpack_nemo_file(self, *, path2file: str, out_folder: str) -> None:
            unpack_calls.append((path2file, out_folder))
            out = Path(out_folder)
            (out / "model_weights.ckpt").write_bytes(b"weights")
            (out / "model_config.yaml").write_text("model: {}\n", encoding="utf-8")

    backend = ParakeetBackend()

    first = backend._ensure_extracted_model_dir(
        model_name="nvidia/parakeet-test",
        model_path=model_path,
        connector=Connector(),
    )
    second = backend._ensure_extracted_model_dir(
        model_name="nvidia/parakeet-test",
        model_path=model_path,
        connector=Connector(),
    )

    assert first == second
    assert (first / ".tara_extract_complete").exists()
    assert (first / "model_weights.ckpt").exists()
    assert len(unpack_calls) == 1


def test_parakeet_backend_reextracts_incomplete_nemo_artifact_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cached .nemo directories must include every artifact referenced by config."""
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("TARA_NEMO_EXTRACT_DIR", str(cache_root))
    model_path = tmp_path / "model.nemo"
    model_path.write_bytes(b"fixture")
    extract_dir = cache_root / f"nvidia_parakeet-test_{model_path.stat().st_size}"
    extract_dir.mkdir(parents=True)
    (extract_dir / ".tara_extract_complete").write_text(str(model_path), encoding="utf-8")
    (extract_dir / "model_weights.ckpt").write_bytes(b"stale weights")
    (extract_dir / "model_config.yaml").write_text(
        "tokenizer:\n  vocab_path: nemo:missing_vocab.txt\n",
        encoding="utf-8",
    )
    unpack_calls: list[tuple[str, str]] = []

    class Connector:
        def _unpack_nemo_file(self, *, path2file: str, out_folder: str) -> None:
            unpack_calls.append((path2file, out_folder))
            out = Path(out_folder)
            (out / "model_weights.ckpt").write_bytes(b"fresh weights")
            (out / "model_config.yaml").write_text(
                "tokenizer:\n  vocab_path: nemo:missing_vocab.txt\n",
                encoding="utf-8",
            )
            (out / "missing_vocab.txt").write_text("token\n", encoding="utf-8")

    backend = ParakeetBackend()

    result = backend._ensure_extracted_model_dir(
        model_name="nvidia/parakeet-test",
        model_path=model_path,
        connector=Connector(),
    )

    assert result == extract_dir
    assert (extract_dir / "missing_vocab.txt").exists()
    assert len(unpack_calls) == 1


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


def test_preload_model_calls_move_to_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """preload_model loads the model and requests device placement."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)
    backend = ParakeetBackend()

    with patch.object(backend, "_move_model_to_device") as move_mock:
        backend.preload_model("parakeet:test-model", device="cuda")

    assert "test-model" in backend._models
    move_mock.assert_called_once_with("test-model", "cuda")


def test_move_models_to_device_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """move_models_to_device relocates every cached model."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)
    backend = ParakeetBackend()
    backend._load_model("test-model")

    with patch.object(backend, "_move_model_to_device") as move_mock:
        backend.move_models_to_device("cpu")

    move_mock.assert_called_once_with("test-model", "cpu")


def test_warmup_transcription_runs_forward_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """warmup_transcription invokes the chunked NeMo path on synthetic silence."""
    dummy_model = _DummyNeMoModel()
    _install_dummy_nemo(monkeypatch, dummy_model)
    backend = ParakeetBackend()
    backend._load_model("test-model")

    with patch(
        "inference_server.parakeet_backend.transcribe_with_nemo_partial_audio",
    ) as mock_transcribe:
        mock_transcribe.return_value = {"text": "", "duration": 1.0}
        backend.warmup_transcription(duration_seconds=1.0, model_name="test-model")

    mock_transcribe.assert_called_once()
    assert mock_transcribe.call_args.kwargs["timestamps"] is False


def test_warmup_transcription_requires_loaded_model() -> None:
    """warmup_transcription fails when no model is cached."""
    backend = ParakeetBackend()
    with pytest.raises(BackendError, match="No model loaded"):
        backend.warmup_transcription()
