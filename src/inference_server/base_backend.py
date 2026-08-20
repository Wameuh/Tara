"""Abstract base class for transcription backends."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from inference_server.models import TranscriptionResponse

LOGGER = logging.getLogger(__name__)


class AbstractTranscriptionBackend(ABC):
    """Abstract base class for transcription backends.

    All transcription backends must inherit from this class and implement
    the abstract methods. This provides a common interface and shared
    functionality for all backend implementations.
    """

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        """Initialize the backend with a logger.

        Args:
            logger: Optional logger instance. If not provided, uses the module logger.
        """
        self._logger = logger or LOGGER

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> TranscriptionResponse:
        """Transcribe the audio file.

        Args:
            audio_path: Path to the audio file to transcribe
            model: Model identifier to use for transcription
            language: Optional language code (e.g., "en", "fr")

        Returns:
            TranscriptionResponse containing the transcribed text, segments, and metadata
        """
        ...  # pragma: no cover

    @abstractmethod
    def stream_transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> tuple[Iterable[Any], Any]:
        """Stream transcription segments as they become available.

        Args:
            audio_path: Path to the audio file to transcribe
            model: Model identifier to use for transcription
            language: Optional language code (e.g., "en", "fr")

        Returns:
            Tuple of (segments_iterable, info_object) where segments_iterable yields
            transcription segments and info_object contains metadata
        """
        ...  # pragma: no cover

    @abstractmethod
    def release_all(self) -> None:
        """Release any cached models and free GPU memory.

        This should clean up resources to prevent memory leaks.
        """
        ...  # pragma: no cover

