"""NeMo Parakeet ASR backend implementation."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from inference_server.base_backend import AbstractTranscriptionBackend
from inference_server.backend import BackendError
from inference_server.models import TranscriptionResponse, TranscriptionSegment
from inference_server.parakeet_utils import (
    ensure_mono_audio,
    iter_nemo_transcription_segments,
    transcribe_with_nemo_partial_audio,
)

LOGGER = logging.getLogger(__name__)


class ParakeetBackend(AbstractTranscriptionBackend):
    """NeMo Parakeet ASR backend adapter."""

    def __init__(
        self,
        *,
        chunk_secs: float = 400.0,
        overlap_percentage: float = 5.0,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the Parakeet backend.

        Args:
            chunk_secs: Length of each chunk in seconds (default: 400.0)
            overlap_percentage: Overlap between chunks as percentage (default: 5.0)
            logger: Optional logger instance
        """
        super().__init__(logger=logger)
        self._chunk_secs = chunk_secs
        self._overlap_percentage = overlap_percentage
        self._models: dict[str, Any] = {}
        self._lock = threading.RLock()

        # Override from environment variables if present
        env_chunk = os.getenv("PARAKEET_CHUNK_SIZE")
        if env_chunk is not None:
            try:
                self._chunk_secs = float(env_chunk)
            except ValueError:
                self._logger.warning("Invalid PARAKEET_CHUNK_SIZE: %s, using default", env_chunk)

        env_overlap = os.getenv("PARAKEET_OVERLAP_PERCENTAGE")
        if env_overlap is not None:
            try:
                self._overlap_percentage = float(env_overlap)
            except ValueError:
                self._logger.warning("Invalid PARAKEET_OVERLAP_PERCENTAGE: %s, using default", env_overlap)

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,  # Not used by NeMo, but kept for API compatibility
    ) -> TranscriptionResponse:
        """Transcribe the audio file using NeMo Parakeet ASR.

        Args:
            audio_path: Path to the audio file to transcribe
            model: Model identifier (should start with "parakeet:", prefix is stripped)
            language: Optional language code (not used by NeMo, kept for API compatibility)

        Returns:
            TranscriptionResponse containing the transcribed text, segments, and metadata

        Raises:
            BackendError: If transcription fails
        """
        # Extract actual model name by removing "parakeet:" prefix
        actual_model_name = model
        if model.startswith("parakeet:"):
            actual_model_name = model[len("parakeet:"):]

        with self._lock:
            nemo_model = self._load_model(actual_model_name)

        temp_mono_path: Path | None = None
        try:
            # Convert audio to mono 16kHz (required for NeMo)
            temp_mono_path = ensure_mono_audio(audio_path)
            self._logger.debug("Converted audio to mono 16kHz: %s", temp_mono_path)

            # Transcribe using chunked approach
            result = transcribe_with_nemo_partial_audio(
                model=nemo_model,
                audio_path=temp_mono_path,
                chunk_secs=self._chunk_secs,
                overlap_percentage=self._overlap_percentage,
                timestamps=True,
                logger=self._logger,
            )

            # Extract text and timestamps
            full_text = result['text']
            segment_timestamps = result.get('segment_timestamps', [])
            duration = result.get('duration', 0.0)

            # Convert timestamp segments to TranscriptionSegment objects
            segments: list[TranscriptionSegment] = []
            for stamp in segment_timestamps:
                if isinstance(stamp, dict):
                    start = float(stamp.get('start', 0.0))
                    end = float(stamp.get('end', 0.0))
                    segment_text = str(stamp.get('segment', '')).strip()
                    if segment_text:  # Only add non-empty segments
                        segments.append(
                            TranscriptionSegment(
                                start=start,
                                end=end,
                                text=segment_text,
                            )
                        )

            return TranscriptionResponse(
                text=full_text,
                segments=segments,
                language=language,  # NeMo doesn't detect language, use provided or None
                duration=duration if duration > 0 else None,
                model=model,
            )

        except BackendError:
            # Re-raise BackendError as-is
            raise
        except Exception as exc:
            message = f"Parakeet transcription failed: {exc}"
            self._logger.error(message, exc_info=True)
            raise BackendError(message) from exc
        finally:
            # Cleanup temporary mono audio file
            if temp_mono_path is not None and temp_mono_path.exists():
                try:
                    temp_mono_path.unlink()
                    self._logger.debug("Cleaned up temporary mono audio file: %s", temp_mono_path)
                except Exception as e:
                    self._logger.warning("Could not delete temporary mono file %s: %s", temp_mono_path, e)

    def stream_transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> tuple[Iterable[Any], Any]:
        """Stream transcription segments and return the info object.

        Note: Currently, NeMo's chunked transcription processes all chunks before returning.
        For now, this returns all segments at once (non-streaming behavior, but API-compatible).

        Args:
            audio_path: Path to the audio file to transcribe
            model: Model identifier (should start with "parakeet:", prefix is stripped)
            language: Optional language code (not used by NeMo, kept for API compatibility)

        Returns:
            Tuple of (segments_iterable, info_object) where segments_iterable yields
            transcription segments and info_object contains metadata
        """
        actual_model_name = model[len("parakeet:"):] if model.startswith("parakeet:") else model
        with self._lock:
            nemo_model = self._load_model(actual_model_name)

        segments_iterable, duration = iter_nemo_transcription_segments(
            model=nemo_model,
            audio_path=audio_path,
            chunk_secs=self._chunk_secs,
            overlap_percentage=self._overlap_percentage,
            timestamps=True,
            logger=self._logger,
        )

        class Info:
            def __init__(self, duration_value: float, language_value: str | None):
                self.duration = duration_value
                self.language = language_value

        info = Info(duration, language)
        return segments_iterable, info

    def _load_model(self, model_name: str) -> Any:
        """Load or reuse a NeMo ASR model instance.

        Args:
            model_name: NeMo model identifier (e.g., "nvidia/parakeet-tdt-0.6b-v3")

        Returns:
            NeMo ASR model instance

        Raises:
            BackendError: If NeMo is not available or model loading fails
        """
        if model_name in self._models:
            return self._models[model_name]

        try:
            import nemo.collections.asr as nemo_asr
        except ImportError as exc:
            message = "Install 'nemo[asr]' or 'nemo-toolkit[asr]' to use Parakeet backend."
            self._logger.debug(message, exc_info=True)
            raise BackendError(message) from exc

        try:
            instance = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
            self._models[model_name] = instance
            self._logger.info("Loaded NeMo model: %s", model_name)
            return instance
        except Exception as exc:  # pragma: no cover - defensive exception handling
            message = f"Failed to load NeMo model '{model_name}': {exc}"
            self._logger.error(message, exc_info=True)
            raise BackendError(message) from exc

    def release_all(self) -> None:
        """Release cached models and free GPU memory.

        Note: NeMo models are PyTorch models, so releasing them properly
        may require moving them to CPU or deleting them explicitly.
        """
        with self._lock:
            # Clear model cache - Python GC should handle cleanup
            # For more aggressive cleanup, we could move models to CPU or delete them
            self._models.clear()
            self._logger.info("Released all cached Parakeet models")







