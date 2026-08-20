"""Faster-Whisper backend implementation."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from inference_server.backend import BackendError
from inference_server.base_backend import AbstractTranscriptionBackend
from inference_server.models import TranscriptionResponse, TranscriptionSegment

LOGGER = logging.getLogger(__name__)


class FasterWhisperBackend(AbstractTranscriptionBackend):
    """Lightweight adapter around faster-whisper."""

    def __init__(
        self,
        *,
        device: str = "cuda",
        compute_type: str = "float16",
        beam_size: int = 5,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the Faster-Whisper backend.

        Args:
            device: Device to use ("cuda" or "cpu")
            compute_type: Compute type for the model
            beam_size: Beam size for decoding
            logger: Optional logger instance
        """
        super().__init__(logger=logger)
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._models: dict[str, Any] = {}
        # Serialize access to the underlying model to avoid concurrent load/release races.
        self._lock = threading.RLock()
        self._apply_windows_openmp_workaround()

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> TranscriptionResponse:
        """Transcribe the audio file using faster-whisper."""

        with self._lock:
            self._log_gpu_usage("pre-transcribe")
            whisper_model = self._load_model(model)
            segments, info = self._invoke_transcribe(
                whisper_model,
                audio_path=audio_path,
                language=language,
            )
        parsed_segments: list[TranscriptionSegment] = []
        for segment in segments:
            parsed_segments.append(
                TranscriptionSegment(
                    start=float(getattr(segment, "start", 0.0) or 0.0),
                    end=float(getattr(segment, "end", 0.0) or 0.0),
                    text=str(getattr(segment, "text", "")).strip(),
                ),
            )

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        language_resolved = getattr(info, "language", None) or language

        response = TranscriptionResponse(
            text=" ".join(segment.text for segment in parsed_segments).strip(),
            segments=parsed_segments,
            language=language_resolved,
            duration=duration,
            model=model,
        )
        self._log_gpu_usage("post-transcribe")
        return response

    def stream_transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> tuple[Iterable[Any], Any]:
        """Stream transcription segments and return the info object."""

        with self._lock:
            self._log_gpu_usage("pre-stream")
            whisper_model = self._load_model(model)
            segments, info = self._invoke_transcribe(
                whisper_model,
                audio_path=audio_path,
                language=language,
            )
            self._log_gpu_usage("post-stream-start")
        return segments, info

    def _invoke_transcribe(
        self,
        whisper_model: Any,
        *,
        audio_path: Path,
        language: str | None,
    ) -> tuple[Iterable[Any], Any]:
        """Invoke the underlying model's transcribe with standard kwargs."""

        try:
            return whisper_model.transcribe(
                str(audio_path),
                beam_size=self._beam_size,
                language=language,
            )
        except Exception as exc:  # pragma: no cover - exercised via tests with stubs
            message = f"faster-whisper failed: {exc}"
            self._logger.debug(message, exc_info=True)
            raise BackendError(message) from exc

    def _load_model(self, model_name: str) -> Any:
        """Load or reuse a Whisper model instance."""

        if model_name in self._models:
            return self._models[model_name]
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - exercised in tests
            message = "Install 'faster-whisper' to use this backend."
            self._logger.debug(message, exc_info=True)
            raise BackendError(message) from exc

        instance = WhisperModel(
            model_name,
            device=self._device,
            compute_type=self._compute_type,
        )
        self._models[model_name] = instance
        return instance

    def release_all(self) -> None:
        """Release cached models and attempt to free GPU memory."""

        with self._lock:
            for model in list(self._models.values()):
                inner = getattr(model, "model", None)
                if inner is not None:
                    unload_model = getattr(inner, "unload_model", None)
                    if callable(unload_model):
                        try:
                            unload_model()
                        except Exception:  # pragma: no cover - best effort
                            self._logger.debug("Unable to unload model weights", exc_info=True)
                    release_cuda = getattr(inner, "release_cuda", None)
                    if callable(release_cuda):
                        try:
                            release_cuda()
                        except Exception:  # pragma: no cover - best effort
                            self._logger.debug("Unable to release CUDA resources", exc_info=True)
                close = getattr(model, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # pragma: no cover - best effort
                        self._logger.debug("Unable to close model", exc_info=True)
            self._models.clear()
            self._log_gpu_usage("post-release")

    @staticmethod
    def _apply_windows_openmp_workaround() -> None:
        """Avoid duplicate OpenMP runtime crashes on Windows."""

        if platform.system().lower() != "windows":
            return
        if os.environ.get("KMP_DUPLICATE_LIB_OK"):
            return
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

    def _log_gpu_usage(self, label: str) -> None:  # pragma: no cover - observational logging
        """Log GPU memory usage via nvidia-smi when available."""

        cmd = [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if not lines:
                self._logger.debug("%s: no GPU data from nvidia-smi", label)
                return
            readings: list[str] = []
            for line in lines:
                parts = [part.strip() for part in line.split(",")]
                if len(parts) != 2:
                    continue
                used, total = parts
                readings.append(f"{used}/{total} MiB")
            if readings:
                self._logger.info("%s: GPU memory %s", label, "; ".join(readings))
            else:
                self._logger.debug("%s: unable to parse nvidia-smi output", label)
        except FileNotFoundError:
            self._logger.debug("%s: nvidia-smi not found; skipping GPU log", label)
        except Exception:  # pragma: no cover - observational logging
            self._logger.debug("%s: failed to read GPU memory", label, exc_info=True)







