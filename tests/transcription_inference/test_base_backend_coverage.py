"""Additional tests for inference_server.base_backend to achieve 100% coverage."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from inference_server.base_backend import AbstractTranscriptionBackend
from inference_server.models import TranscriptionResponse, TranscriptionSegment


class ConcreteBackend(AbstractTranscriptionBackend):
    """Concrete implementation for testing abstract methods."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> TranscriptionResponse:
        """Concrete implementation of transcribe."""
        return TranscriptionResponse(
            text="test",
            segments=[TranscriptionSegment(start=0.0, end=1.0, text="test")],
            language=language or "en",
            duration=1.0,
            model=model,
        )

    def stream_transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> tuple[Iterable[Any], Any]:
        """Concrete implementation of stream_transcribe."""
        segments = iter([TranscriptionSegment(start=0.0, end=1.0, text="test")])

        class Info:
            def __init__(self, lang: str | None) -> None:
                self.duration = 1.0
                self.language = lang or "en"

        return segments, Info(language)

    def release_all(self) -> None:
        """Concrete implementation of release_all."""
        pass


def test_abstract_backend_init_with_logger() -> None:
    """Test AbstractTranscriptionBackend initialization with custom logger."""
    custom_logger = logging.getLogger("test_logger")
    backend = ConcreteBackend(logger=custom_logger)
    assert backend._logger == custom_logger


def test_abstract_backend_init_without_logger() -> None:
    """Test AbstractTranscriptionBackend initialization without logger."""
    backend = ConcreteBackend()
    assert backend._logger is not None
    assert isinstance(backend._logger, logging.Logger)


def test_abstract_backend_cannot_instantiate() -> None:
    """Test that AbstractTranscriptionBackend cannot be instantiated directly."""
    with pytest.raises(TypeError):
        AbstractTranscriptionBackend()  # type: ignore[abstract]


def test_concrete_backend_transcribe() -> None:
    """Test concrete backend implements transcribe correctly."""
    backend = ConcreteBackend()
    result = backend.transcribe(Path("test.mp3"), model="test", language="en")
    assert result.text == "test"
    assert result.model == "test"
    assert result.language == "en"


def test_concrete_backend_stream_transcribe() -> None:
    """Test concrete backend implements stream_transcribe correctly."""
    backend = ConcreteBackend()
    segments, info = backend.stream_transcribe(Path("test.mp3"), model="test", language="en")
    assert hasattr(info, "duration")
    assert hasattr(info, "language")
    segments_list = list(segments)
    assert len(segments_list) > 0


def test_concrete_backend_release_all() -> None:
    """Test concrete backend implements release_all correctly."""
    backend = ConcreteBackend()
    # Should not raise an exception
    backend.release_all()

