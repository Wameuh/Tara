"""Tests for Modal GPU snapshot lifecycle on ParakeetTranscriber."""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modal_inference import (  # noqa: E402
    DEFAULT_PARAKEET_MODEL,
    ENABLE_GPU_SNAPSHOT,
    ENABLE_MEMORY_SNAPSHOT,
    TranscriptionJob,
    _parakeet_transcriber_cls_kwargs,
    _preload_snapshot_backend,
    _restore_snapshot_backend,
    _run_transcription_job,
)


def test_parakeet_transcriber_cls_kwargs_enable_snapshots() -> None:
    """Cls decorator kwargs include memory and GPU snapshot flags."""
    kwargs = _parakeet_transcriber_cls_kwargs()
    assert kwargs["startup_timeout"] == 2400
    assert kwargs["max_containers"] == 3
    if ENABLE_MEMORY_SNAPSHOT:
        assert kwargs.get("enable_memory_snapshot") is True
    if ENABLE_GPU_SNAPSHOT:
        assert kwargs.get("experimental_options") == {"enable_gpu_snapshot": True}


def test_snapshot_flags_default_enabled() -> None:
    """GPU snapshot flags are enabled by default in deployment config."""
    assert ENABLE_MEMORY_SNAPSHOT is True
    assert ENABLE_GPU_SNAPSHOT is True


def test_preload_snapshot_backend_gpu_path() -> None:
    """GPU snapshot preload loads on CUDA and warms up."""
    backend = MagicMock()

    _preload_snapshot_backend(backend, gpu_snapshot=True)

    backend.preload_model.assert_called_once_with(DEFAULT_PARAKEET_MODEL, device="cuda")
    backend.warmup_transcription.assert_called_once_with(
        duration_seconds=1.0,
        model_name=DEFAULT_PARAKEET_MODEL,
    )


def test_preload_snapshot_backend_cpu_fallback_path() -> None:
    """CPU snapshot preload loads weights on CPU only."""
    backend = MagicMock()

    _preload_snapshot_backend(backend, gpu_snapshot=False)

    backend.preload_model.assert_called_once_with(DEFAULT_PARAKEET_MODEL, device="cpu")
    backend.warmup_transcription.assert_not_called()


def test_restore_snapshot_backend_cpu_moves_to_cuda() -> None:
    """CPU snapshot restore moves models to CUDA and warms up."""
    backend = MagicMock()

    _restore_snapshot_backend(backend, gpu_snapshot=False)

    backend.move_models_to_device.assert_called_once_with("cuda")
    backend.warmup_transcription.assert_called_once_with(
        duration_seconds=1.0,
        model_name=DEFAULT_PARAKEET_MODEL,
    )


def test_restore_snapshot_backend_gpu_is_noop() -> None:
    """GPU snapshot restore does not reload or move models."""
    backend = MagicMock()

    _restore_snapshot_backend(backend, gpu_snapshot=True)

    backend.move_models_to_device.assert_not_called()
    backend.warmup_transcription.assert_not_called()


def test_run_transcription_job_returns_result(tmp_path: Path) -> None:
    """Shared runner validates a staged reference and returns a result."""
    backend = MagicMock()
    response = MagicMock()
    response.model_dump.return_value = {"text": "hello"}
    backend.transcribe.return_value = response

    run_id = "run_0000000000001"
    storage_path = f"runs/{run_id}/audio_000000000001.ogg"
    staged = tmp_path / storage_path
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"\x00\x01")
    job = TranscriptionJob(
        run_id=run_id,
        file_index=2,
        relative_path="clip.ogg",
        storage_path=storage_path,
        size_bytes=2,
        sha256_hex=hashlib.sha256(b"\x00\x01").hexdigest(),
        expires_at=(datetime.now(UTC) + timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z"),
        model="parakeet:nvidia/parakeet-tdt-0.6b-v3",
        language="fr",
    )

    progress: dict[int, dict[str, Any]] = {}

    class FakeDict:
        def __getitem__(self, key: int) -> dict[str, Any]:
            return progress[key]

        def __setitem__(self, key: int, value: dict[str, Any]) -> None:
            progress[key] = value

    with (
        patch("modal_inference.modal.Dict.from_name", return_value=FakeDict()),
        patch(
            "modal_inference.os.getenv",
            return_value="task-123",
        ),
        patch("modal_inference.AUDIO_STAGING_DIR", str(tmp_path)),
    ):
        result = _run_transcription_job(backend, job)

    assert result.file_index == 2
    assert result.relative_path == "clip.ogg"
    assert result.payload == {"text": "hello"}
    assert result.modal_task_id == "task-123"
    backend.transcribe.assert_called_once()


def _staged_job(tmp_path: Path, content: bytes = b"audio") -> TranscriptionJob:
    run_id = "run_0000000000001"
    storage_path = f"runs/{run_id}/audio_000000000001.ogg"
    staged = tmp_path / storage_path
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(content)
    return TranscriptionJob(
        run_id=run_id,
        file_index=1,
        relative_path="clip.ogg",
        storage_path=storage_path,
        size_bytes=len(content),
        sha256_hex=hashlib.sha256(content).hexdigest(),
        expires_at=(datetime.now(UTC) + timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z"),
        model="parakeet:nvidia/parakeet-tdt-0.6b-v3",
        language="fr",
    )


def test_run_transcription_job_rejects_expired_or_tampered_reference(
    tmp_path: Path,
) -> None:
    progress: dict[int, dict[str, Any]] = {}

    class FakeDict:
        def __setitem__(self, key: int, value: dict[str, Any]) -> None:
            progress[key] = value

    valid = _staged_job(tmp_path)
    expired = TranscriptionJob(
        **{
            **valid.__dict__,
            "expires_at": "2020-01-01T00:00:00Z",
        }
    )
    tampered = TranscriptionJob(
        **{
            **valid.__dict__,
            "sha256_hex": "0" * 64,
        }
    )
    with (
        patch("modal_inference.modal.Dict.from_name", return_value=FakeDict()),
        patch("modal_inference.AUDIO_STAGING_DIR", str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="expired"):
            _run_transcription_job(MagicMock(), expired)
        with pytest.raises(ValueError, match="invalid audio reference"):
            _run_transcription_job(MagicMock(), tampered)


def test_run_transcription_job_normalizes_missing_reference_error(
    tmp_path: Path,
) -> None:
    job = _staged_job(tmp_path)
    (tmp_path / job.storage_path).unlink()

    with (
        patch("modal_inference.modal.Dict.from_name", return_value=MagicMock()),
        patch("modal_inference.AUDIO_STAGING_DIR", str(tmp_path)),
    ):
        with pytest.raises(ValueError, match="invalid audio reference"):
            _run_transcription_job(MagicMock(), job)


def test_run_transcription_job_rejects_oversized_response(tmp_path: Path) -> None:
    progress: dict[int, dict[str, Any]] = {}

    class FakeDict:
        def __setitem__(self, key: int, value: dict[str, Any]) -> None:
            progress[key] = value

    backend = MagicMock()
    response = MagicMock()
    response.model_dump.return_value = {"text": "x" * 64, "segments": []}
    backend.transcribe.return_value = response
    with (
        patch("modal_inference.modal.Dict.from_name", return_value=FakeDict()),
        patch("modal_inference.AUDIO_STAGING_DIR", str(tmp_path)),
        patch("modal_inference._MAX_RESULT_BYTES", 16),
    ):
        with pytest.raises(ValueError, match="exceeds"):
            _run_transcription_job(backend, _staged_job(tmp_path))
