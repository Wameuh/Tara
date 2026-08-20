"""NeMo Parakeet ASR backend implementation."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np

from inference_server.backend import BackendError
from inference_server.base_backend import AbstractTranscriptionBackend
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

    @staticmethod
    def _strip_parakeet_prefix(model_name: str) -> str:
        """Return the bare NeMo model id without the ``parakeet:`` prefix."""
        if model_name.startswith("parakeet:"):
            return model_name[len("parakeet:") :]
        return model_name

    def preload_model(self, model_name: str, *, device: str = "cuda") -> None:
        """Load a NeMo ASR model onto ``device`` for Modal snapshot initialization.

        Args:
            model_name: NeMo model id, optionally prefixed with ``parakeet:``.
            device: PyTorch device string (``cuda``, ``cpu``, etc.).

        Raises:
            BackendError: If loading or device placement fails.
        """
        actual_model_name = self._strip_parakeet_prefix(model_name)
        with self._lock:
            self._load_model(actual_model_name)
            if device:
                self._move_model_to_device(actual_model_name, device)
        self._logger.info(
            "Preloaded NeMo model %s on device=%s",
            actual_model_name,
            device,
        )

    def move_models_to_device(self, device: str) -> None:
        """Move all cached NeMo models to ``device`` (CPU snapshot fallback path).

        Args:
            device: PyTorch device string (``cuda``, ``cpu``, etc.).
        """
        with self._lock:
            for model_name in list(self._models):
                self._move_model_to_device(model_name, device)
        self._logger.info("Moved %s cached model(s) to device=%s", len(self._models), device)

    def warmup_transcription(
        self,
        *,
        duration_seconds: float = 1.0,
        model_name: str | None = None,
    ) -> None:
        """Run a short forward pass on synthetic silence to warm CUDA kernels.

        Args:
            duration_seconds: Length of the synthetic audio clip in seconds.
            model_name: NeMo model id to warm; defaults to the first cached model.

        Raises:
            BackendError: If no model is loaded or warmup transcription fails.
        """
        import soundfile as sf

        if duration_seconds <= 0:
            message = f"warmup duration must be positive, got {duration_seconds}"
            raise BackendError(message)

        with self._lock:
            if model_name is not None:
                actual_model_name = self._strip_parakeet_prefix(model_name)
            elif self._models:
                actual_model_name = next(iter(self._models))
            else:
                raise BackendError("No model loaded for warmup transcription")
            if actual_model_name not in self._models:
                raise BackendError(
                    f"Model '{actual_model_name}' is not loaded for warmup",
                )
            nemo_model = self._models[actual_model_name]

        sample_rate = 16000
        samples = max(1, int(sample_rate * duration_seconds))
        silence = np.zeros(samples, dtype=np.float32)
        temp_path = Path(tempfile.mkstemp(suffix=".wav")[1])
        try:
            sf.write(str(temp_path), silence, sample_rate, format="WAV")
            transcribe_with_nemo_partial_audio(
                model=nemo_model,
                audio_path=temp_path,
                chunk_secs=max(duration_seconds + 0.5, 1.0),
                overlap_percentage=self._overlap_percentage,
                timestamps=False,
                logger=self._logger,
            )
            self._logger.info(
                "Warmup transcription completed for %s (%.1fs silence)",
                actual_model_name,
                duration_seconds,
            )
        except BackendError:
            raise
        except Exception as exc:
            message = f"Warmup transcription failed: {exc}"
            self._logger.error(message, exc_info=True)
            raise BackendError(message) from exc
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError as exc:
                    self._logger.warning("Could not delete warmup audio %s: %s", temp_path, exc)

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,  # Not used by NeMo, but kept for API compatibility
        chunk_progress_callback: Callable[[int, int], None] | None = None,
    ) -> TranscriptionResponse:
        """Transcribe the audio file using NeMo Parakeet ASR.

        Args:
            audio_path: Path to the audio file to transcribe
            model: Model identifier (should start with "parakeet:", prefix is stripped)
            language: Optional language code (not used by NeMo, kept for API compatibility)
            chunk_progress_callback: Optional callback invoked after each audio chunk.

        Returns:
            TranscriptionResponse containing the transcribed text, segments, and metadata

        Raises:
            BackendError: If transcription fails
        """
        # Extract actual model name by removing "parakeet:" prefix
        actual_model_name = self._strip_parakeet_prefix(model)

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
                chunk_progress_callback=chunk_progress_callback,
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
        actual_model_name = self._strip_parakeet_prefix(model)
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
            connector = self._build_persistent_restore_connector(
                nemo_asr.models.ASRModel,
                model_name,
            )
            if connector is None:
                instance = nemo_asr.models.ASRModel.from_pretrained(
                    model_name=model_name,
                )
            else:
                instance = nemo_asr.models.ASRModel.from_pretrained(
                    model_name=model_name,
                    save_restore_connector=connector,
                )
            self._models[model_name] = instance
            self._logger.info("Loaded NeMo model: %s", model_name)
            return instance
        except Exception as exc:  # pragma: no cover - defensive exception handling
            message = f"Failed to load NeMo model '{model_name}': {exc}"
            self._logger.error(message, exc_info=True)
            raise BackendError(message) from exc

    def _move_model_to_device(self, model_name: str, device: str) -> None:
        """Move one cached NeMo model instance to ``device``."""
        instance = self._models.get(model_name)
        if instance is None:
            return

        normalized = device.lower()
        try:
            if normalized in {"cuda", "gpu"}:
                if hasattr(instance, "cuda"):
                    instance.cuda()
                    return
            elif normalized == "cpu":
                if hasattr(instance, "cpu"):
                    instance.cpu()
                    return

            import torch

            instance.to(torch.device(normalized))
        except Exception as exc:
            message = f"Failed to move NeMo model '{model_name}' to {device}: {exc}"
            self._logger.error(message, exc_info=True)
            raise BackendError(message) from exc

    def _build_persistent_restore_connector(
        self,
        asr_model_cls: Any,
        model_name: str,
    ) -> Any | None:
        """Extract cached .nemo models outside NeMo's short-lived temp dir."""
        try:
            model_file = asr_model_cls.from_pretrained(
                model_name=model_name,
                return_model_file=True,
            )
        except TypeError:
            return None
        model_path = Path(str(model_file))
        if not model_path.is_file() or model_path.suffix.lower() != ".nemo":
            return None

        try:
            from nemo.core.connectors.save_restore_connector import SaveRestoreConnector
        except ImportError:
            return None

        connector = SaveRestoreConnector()
        extract_dir = self._ensure_extracted_model_dir(
            model_name=model_name,
            model_path=model_path,
            connector=connector,
        )
        connector.model_extracted_dir = str(extract_dir)
        return connector

    def _ensure_extracted_model_dir(
        self,
        *,
        model_name: str,
        model_path: Path,
        connector: Any,
    ) -> Path:
        root = Path(
            os.getenv(
                "TARA_NEMO_EXTRACT_DIR",
                str(Path(tempfile.gettempdir()) / "tara_nemo_models"),
            ),
        )
        root.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name).strip("_")
        extract_dir = root / f"{safe_name}_{model_path.stat().st_size}"
        if self._is_extracted_model_dir_complete(extract_dir):
            return extract_dir

        resolved_root = root.resolve()
        resolved_extract = (
            extract_dir.resolve() if extract_dir.exists() else extract_dir
        )
        if extract_dir.exists() and resolved_root not in resolved_extract.parents:
            raise BackendError(
                f"Refusing to clear unexpected model cache: {extract_dir}",
            )
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        self._logger.info("Extracting NeMo model cache to %s", extract_dir)
        connector._unpack_nemo_file(
            path2file=str(model_path),
            out_folder=str(extract_dir),
        )
        marker = extract_dir / ".tara_extract_complete"
        marker.write_text(str(model_path), encoding="utf-8")
        return extract_dir

    def _is_extracted_model_dir_complete(self, extract_dir: Path) -> bool:
        marker = extract_dir / ".tara_extract_complete"
        weights = extract_dir / "model_weights.ckpt"
        config = extract_dir / "model_config.yaml"
        if not (marker.exists() and weights.exists() and config.exists()):
            return False

        missing_artifacts = [
            artifact
            for artifact in self._required_nemo_artifacts(config)
            if not (extract_dir / artifact).is_file()
        ]
        if missing_artifacts:
            self._logger.warning(
                "NeMo model cache at %s is missing artifacts: %s",
                extract_dir,
                ", ".join(sorted(missing_artifacts)),
            )
            return False
        return True

    @staticmethod
    def _required_nemo_artifacts(config_path: Path) -> set[str]:
        """Return artifact filenames referenced by `nemo:` config paths."""
        text = config_path.read_text(encoding="utf-8")
        return set(re.findall(r"nemo:([A-Za-z0-9_.-]+)", text))

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



