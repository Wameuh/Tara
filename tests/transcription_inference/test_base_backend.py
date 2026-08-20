"""Tests for AbstractTranscriptionBackend base class."""

from __future__ import annotations

import pytest

from inference_server.base_backend import AbstractTranscriptionBackend


def test_base_backend_is_abstract() -> None:
    """Cannot instantiate AbstractTranscriptionBackend directly."""

    # Should raise TypeError because abstract methods are not implemented
    with pytest.raises(TypeError):
        AbstractTranscriptionBackend()  # type: ignore[abstract]


def test_base_backend_initializes_logger() -> None:
    """Logger is set correctly."""

    class ConcreteBackend(AbstractTranscriptionBackend):
        def transcribe(self, audio_path, *, model, language):
            return None  # type: ignore[return-value]

        def stream_transcribe(self, audio_path, *, model, language):
            return iter([]), None

        def release_all(self) -> None:
            pass

    backend = ConcreteBackend()
    assert backend._logger is not None
    assert backend._logger.name == "inference_server.base_backend"






