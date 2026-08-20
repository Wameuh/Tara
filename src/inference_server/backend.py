"""Transcription backend protocol and factory."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from inference_server.models import TranscriptionResponse


class BackendError(RuntimeError):
    """Raised when the backend cannot process a transcription request."""


class TranscriptionBackend(Protocol):
    """Protocol for transcription backends."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> TranscriptionResponse:
        """Transcribe ``audio_path``."""

    def stream_transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> tuple[Iterable[Any], Any]:
        """Stream transcription segments and return the info object."""

    def release_all(self) -> None:
        """Release any cached models and GPU memory."""


# Singleton instances
_PARAKET_BACKEND: Any | None = None
_FASTER_WHISPER_BACKEND: Any | None = None


def _get_parakeet_backend() -> Any:
    """Get or create singleton ParakeetBackend instance."""
    global _PARAKET_BACKEND
    if _PARAKET_BACKEND is None:
        from inference_server.parakeet_backend import ParakeetBackend

        _PARAKET_BACKEND = ParakeetBackend()
    return _PARAKET_BACKEND


def _get_faster_whisper_backend() -> Any:
    """Get or create singleton FasterWhisperBackend instance."""
    global _FASTER_WHISPER_BACKEND
    if _FASTER_WHISPER_BACKEND is None:
        from inference_server.faster_whisper_backend import FasterWhisperBackend

        _FASTER_WHISPER_BACKEND = FasterWhisperBackend()
    return _FASTER_WHISPER_BACKEND


def create_backend(model: str) -> TranscriptionBackend:
    """Create appropriate backend based on model name prefix.

    Args:
        model: Model identifier. If it starts with "parakeet:", uses ParakeetBackend,
               otherwise uses FasterWhisperBackend.

    Returns:
        Appropriate backend instance implementing TranscriptionBackend protocol.
    """
    if model.startswith("parakeet:"):
        return _get_parakeet_backend()
    return _get_faster_whisper_backend()

