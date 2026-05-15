"""Tests for FasterWhisperBackend."""

from __future__ import annotations

import sys
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from inference_server.backend import BackendError
from inference_server.faster_whisper_backend import FasterWhisperBackend


class _DummySegment:
    def __init__(self) -> None:
        self.start = 0.0
        self.end = 1.0
        self.text = "hi"


class _DummyInfo:
    def __init__(self) -> None:
        self.language = "en"
        self.language_probability = 0.9
        self.duration = 1.0


class _DummyModel:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def transcribe(self, audio_path: str, *, beam_size: int, language: str | None):
        self.calls.append(Path(audio_path))
        return [_DummySegment()], _DummyInfo()


def _install_dummy_whisper(monkeypatch: pytest.MonkeyPatch, model: _DummyModel) -> None:
    module = ModuleType("faster_whisper")
    module.WhisperModel = lambda *args, **kwargs: model  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def test_backend_transcribes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dummy = _DummyModel()
    _install_dummy_whisper(monkeypatch, dummy)

    backend = FasterWhisperBackend(device="cpu", compute_type="int8", beam_size=1)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"\x00\x00")

    result = backend.transcribe(audio, model="large-v3", language="en")

    assert result.text == "hi"
    assert result.language == "en"
    assert result.duration == 1.0
    assert dummy.calls == [audio]


def test_backend_missing_dependency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original_import = __import__

    def fake_import(name: str, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr("builtins.__import__", fake_import)
    # exercise non-faster_whisper branch
    fake_import("os")
    backend = FasterWhisperBackend(device="cpu", compute_type="int8", beam_size=1)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"\x00\x00")

    with pytest.raises(BackendError, match="Install 'faster-whisper'"):
        backend.transcribe(audio, model="large-v3", language=None)


def test_backend_release_all(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = _DummyModel()
    _install_dummy_whisper(monkeypatch, dummy)
    backend = FasterWhisperBackend(device="cpu", compute_type="int8", beam_size=1)
    backend._models["x"] = dummy  # type: ignore[assignment]

    backend.release_all()

    assert backend._models == {}


def test_backend_stream_transcribe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model = _DummyModel()
    _install_dummy_whisper(monkeypatch, model)
    backend = FasterWhisperBackend(device="cpu", compute_type="int8", beam_size=1)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"\x00\x00")

    segments, info = backend.stream_transcribe(audio, model="large-v3", language="en")
    collected = list(segments)

    assert len(collected) == 1
    assert info.language == "en"


def test_backend_release_all_calls_inner(monkeypatch: pytest.MonkeyPatch) -> None:
    class Inner:
        def __init__(self) -> None:
            self.unloaded = False
            self.released = False

        def unload_model(self) -> None:
            self.unloaded = True

        def release_cuda(self) -> None:
            self.released = True

    class Wrapper:
        def __init__(self) -> None:
            self.model = Inner()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    model = Wrapper()
    _install_dummy_whisper(monkeypatch, model)  # type: ignore[arg-type]
    backend = FasterWhisperBackend(device="cpu", compute_type="int8", beam_size=1)
    backend._models["x"] = model  # type: ignore[assignment]

    backend.release_all()

    assert backend._models == {}
    assert model.model.unloaded is True
    assert model.model.released is True
    assert model.closed is True


def test_backend_load_model_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = _DummyModel()
    _install_dummy_whisper(monkeypatch, dummy)
    backend = FasterWhisperBackend(device="cpu", compute_type="int8", beam_size=1)

    first = backend._load_model("x")
    second = backend._load_model("x")

    assert first is second


def test_backend_windows_openmp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KMP_DUPLICATE_LIB_OK", "")
    monkeypatch.setattr("inference_server.faster_whisper_backend.platform.system", lambda: "Windows")
    monkeypatch.delenv("KMP_DUPLICATE_LIB_OK", raising=False)

    FasterWhisperBackend._apply_windows_openmp_workaround()

    assert os.environ["KMP_DUPLICATE_LIB_OK"] == "TRUE"


def test_backend_openmp_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("inference_server.faster_whisper_backend.platform.system", lambda: "Linux")
    monkeypatch.delenv("KMP_DUPLICATE_LIB_OK", raising=False)

    FasterWhisperBackend._apply_windows_openmp_workaround()

    assert "KMP_DUPLICATE_LIB_OK" not in os.environ






