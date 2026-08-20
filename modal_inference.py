"""Modal deployment entrypoint for the Tara Parakeet inference server."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import modal

LOGGER = logging.getLogger(__name__)

APP_NAME = "tara-parakeet-inference"
CACHE_VOLUME_NAME = "tara-parakeet-cache"
MODEL_CACHE_DIR = "/model-cache"

DEFAULT_PARAKEET_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
DEFAULT_PARAKEET_MODEL_WITH_PREFIX = f"parakeet:{DEFAULT_PARAKEET_MODEL}"
# Pinned Hugging Face revision for reproducible warm downloads.
# Bump in this file when intentionally upgrading the cached weights.
PARAKEET_HF_REVISION = "528cdc59bb15ff85234fa8dda1855b60b2d6a0ca"

DEFAULT_MAX_MODAL_AUDIO_BYTES = 1024 * 1024 * 1024
_MAX_CONTAINERS = 3
_FASTAPI_MAX_CONTAINERS = 5
_BUFFER_CONTAINERS = 0
_SCALEDOWN_WINDOW_SECONDS = 10
_STARTUP_TIMEOUT_SECONDS = 2400
ENABLE_MEMORY_SNAPSHOT = True
ENABLE_GPU_SNAPSHOT = True
MODAL_CLASS_NAME = "ParakeetTranscriber"
MODAL_FUNCTION_NAME = "transcribe_one"
AUDIO_STAGING_VOLUME_NAME = "tara-audio-staging"
AUDIO_STAGING_DIR = "/mnt/tara-audio-staging"
_STAGING_PATH = re.compile(
    r"^runs/[A-Za-z0-9_-]{16,128}/[A-Za-z0-9_-]{16,128}\.[a-z0-9]{1,8}$"
)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_RESULT_BYTES = 32 * 1024 * 1024
_MAX_RESULT_SEGMENTS = 200_000
_MAX_SEGMENT_TEXT_CHARS = 1_000_000


@dataclass(frozen=True)
class TranscriptionJob:
    """Serializable input for one Modal ``transcribe_one`` invocation."""

    run_id: str
    file_index: int
    relative_path: str
    storage_path: str
    size_bytes: int
    sha256_hex: str
    expires_at: str
    model: str
    language: str | None


@dataclass(frozen=True)
class TranscriptionJobResult:
    """Result returned from Modal to the local Tara process."""

    file_index: int
    relative_path: str
    payload: dict[str, object]
    modal_task_id: str | None
    processing_time_seconds: float


IMAGE_ENV: dict[str, str] = {
    "PYTHONPATH": "/root",
    "INFERENCE_USE_WORKER": "0",
    "INFERENCE_DISABLE_RELEASE": "1",
    "INFERENCE_LOG_LEVEL": "INFO",
    "PARAKEET_CHUNK_SIZE": "400",
    "PARAKEET_OVERLAP_PERCENTAGE": "5",
    "HF_HOME": f"{MODEL_CACHE_DIR}/huggingface",
    "TARA_NEMO_EXTRACT_DIR": f"{MODEL_CACHE_DIR}/nemo-extract",
    "HF_XET_HIGH_PERFORMANCE": "1",
}

cache_volume = modal.Volume.from_name(CACHE_VOLUME_NAME, create_if_missing=True)
audio_staging_volume = modal.Volume.from_name(
    AUDIO_STAGING_VOLUME_NAME, create_if_missing=True
)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "fastapi[standard]>=0.115",
        "uvicorn>=0.32",
        "numpy>=1.26",
        "pydantic>=2.12",
        "requests>=2.32",
        "faster-whisper>=1.0.0",
        "librosa>=0.10.0",
        "soundfile>=0.12.0",
        "huggingface_hub>=0.26",
        "nemo_toolkit[asr]",
    )
    .env(IMAGE_ENV)
    .add_local_dir("src/inference_server", remote_path="/root/inference_server")
)

with image.imports():
    import nemo.collections.asr  # noqa: F401
    import torch  # noqa: F401

app = modal.App(APP_NAME)


class WarmCacheError(RuntimeError):
    """Raised when Parakeet model cache warming fails."""


def _normalize_parakeet_model_name(model_name: str) -> str:
    """Strip the optional ``parakeet:`` prefix from a model identifier."""
    if model_name.startswith("parakeet:"):
        return model_name[len("parakeet:") :]
    return model_name


def _safe_model_dir_name(model_name: str) -> str:
    """Return a filesystem-safe directory name for a NeMo model id."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name).strip("_")


def _required_nemo_artifacts(config_path: Path) -> set[str]:
    """Return artifact filenames referenced by ``nemo:`` config paths.

    Mirrors ``ParakeetBackend._required_nemo_artifacts`` for warm-cache checks.
    """
    text = config_path.read_text(encoding="utf-8")
    return set(re.findall(r"nemo:([A-Za-z0-9_.-]+)", text))


def _is_extracted_model_dir_complete(extract_dir: Path) -> bool:
    """Return whether a NeMo extract directory contains all required artifacts.

    Mirrors ``ParakeetBackend._is_extracted_model_dir_complete``.
    """
    marker = extract_dir / ".tara_extract_complete"
    weights = extract_dir / "model_weights.ckpt"
    config = extract_dir / "model_config.yaml"
    if not (marker.exists() and weights.exists() and config.exists()):
        return False

    missing_artifacts = [
        artifact
        for artifact in _required_nemo_artifacts(config)
        if not (extract_dir / artifact).is_file()
    ]
    return not missing_artifacts


def _find_complete_extract_dir(
    nemo_extract_dir: Path,
    model_name: str,
) -> Path | None:
    """Return an existing complete extract directory for ``model_name``, if any."""
    root = nemo_extract_dir
    if not root.is_dir():
        return None

    safe_name = _safe_model_dir_name(model_name)
    prefix = f"{safe_name}_"
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir() or not candidate.name.startswith(prefix):
            continue
        if _is_extracted_model_dir_complete(candidate):
            return candidate
    return None


def _download_nemo_archive(
    model_name: str,
    *,
    revision: str,
    hf_home: Path,
) -> Path:
    """Download the ``.nemo`` archive for ``model_name`` into ``hf_home``."""
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_home)

    from huggingface_hub import snapshot_download

    local_dir = Path(
        snapshot_download(
            repo_id=model_name,
            revision=revision,
            local_dir=hf_home / _safe_model_dir_name(model_name),
        ),
    )
    nemo_files = sorted(local_dir.rglob("*.nemo"))
    if nemo_files:
        return nemo_files[0]

    try:
        import nemo.collections.asr as nemo_asr
    except ImportError as exc:
        message = "Install 'nemo_toolkit[asr]' to warm Parakeet model caches on Modal."
        raise WarmCacheError(message) from exc

    try:
        model_file = nemo_asr.models.ASRModel.from_pretrained(
            model_name=model_name,
            return_model_file=True,
        )
    except TypeError as exc:
        message = (
            f"NeMo from_pretrained does not support return_model_file for "
            f"'{model_name}'"
        )
        raise WarmCacheError(message) from exc

    model_path = Path(str(model_file))
    if not model_path.is_file() or model_path.suffix.lower() != ".nemo":
        message = f"Expected a .nemo file for '{model_name}', got '{model_path}'"
        raise WarmCacheError(message)
    return model_path


def _ensure_extracted_model_dir(
    *,
    model_name: str,
    model_path: Path,
    nemo_extract_dir: Path,
    connector: Any,
) -> Path:
    """Extract ``model_path`` into a persistent directory under ``nemo_extract_dir``.

    Mirrors ``ParakeetBackend._ensure_extracted_model_dir``.
    """
    root = nemo_extract_dir
    root.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_model_dir_name(model_name)
    extract_dir = root / f"{safe_name}_{model_path.stat().st_size}"
    if _is_extracted_model_dir_complete(extract_dir):
        return extract_dir

    resolved_root = root.resolve()
    resolved_extract = extract_dir.resolve() if extract_dir.exists() else extract_dir
    if extract_dir.exists() and resolved_root not in resolved_extract.parents:
        message = f"Refusing to clear unexpected model cache: {extract_dir}"
        raise WarmCacheError(message)

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Extracting NeMo model cache to %s", extract_dir)
    connector._unpack_nemo_file(
        path2file=str(model_path),
        out_folder=str(extract_dir),
    )
    marker = extract_dir / ".tara_extract_complete"
    marker.write_text(str(model_path), encoding="utf-8")
    return extract_dir


def _warm_parakeet_cache(
    model_name: str,
    *,
    revision: str,
    hf_home: Path,
    nemo_extract_dir: Path,
) -> tuple[Path, bool]:
    """Download and extract Parakeet weights when the cache is incomplete.

    Args:
        model_name: NeMo model id, optionally prefixed with ``parakeet:``.
        revision: Pinned Hugging Face revision (commit, tag, or branch).
        hf_home: Hugging Face cache root (``HF_HOME`` on Modal).
        nemo_extract_dir: Persistent NeMo extract root (``TARA_NEMO_EXTRACT_DIR``).

    Returns:
        Tuple of the extract directory and whether the warm step was skipped.

    Raises:
        WarmCacheError: If download or extraction fails.
    """
    actual_model_name = _normalize_parakeet_model_name(model_name)
    existing = _find_complete_extract_dir(nemo_extract_dir, actual_model_name)
    if existing is not None:
        LOGGER.info("Parakeet cache already complete at %s", existing)
        return existing, True

    model_path = _download_nemo_archive(
        actual_model_name,
        revision=revision,
        hf_home=hf_home,
    )

    try:
        from nemo.core.connectors.save_restore_connector import SaveRestoreConnector
    except ImportError as exc:
        message = "Install 'nemo_toolkit[asr]' to extract NeMo model archives."
        raise WarmCacheError(message) from exc

    connector = SaveRestoreConnector()
    extract_dir = _ensure_extracted_model_dir(
        model_name=actual_model_name,
        model_path=model_path,
        nemo_extract_dir=nemo_extract_dir,
        connector=connector,
    )
    return extract_dir, False


@app.function(
    image=image,
    volumes={MODEL_CACHE_DIR: cache_volume},
    timeout=3600,
)
def warm_cache(
    model: str = DEFAULT_PARAKEET_MODEL_WITH_PREFIX,
    revision: str = PARAKEET_HF_REVISION,
) -> str:
    """Pre-populate the Modal Volume with Parakeet model weights."""
    hf_home = Path(os.environ.get("HF_HOME", f"{MODEL_CACHE_DIR}/huggingface"))
    nemo_extract_dir = Path(
        os.environ.get("TARA_NEMO_EXTRACT_DIR", f"{MODEL_CACHE_DIR}/nemo-extract"),
    )
    cache_dir, skipped = _warm_parakeet_cache(
        model,
        revision=revision,
        hf_home=hf_home,
        nemo_extract_dir=nemo_extract_dir,
    )
    cache_volume.commit()
    return f"skipped={skipped} path={cache_dir}"


@app.local_entrypoint()
def warm_cache_entrypoint(model: str = DEFAULT_PARAKEET_MODEL_WITH_PREFIX) -> None:
    """Run ``warm_cache`` remotely from the Modal CLI."""
    print(warm_cache.remote(model))


def _parakeet_transcriber_cls_kwargs() -> dict[str, Any]:
    """Build decorator kwargs for ``ParakeetTranscriber`` including snapshot flags."""
    kwargs: dict[str, Any] = {
        "image": image,
        "gpu": ["L4", "A10"],
        "timeout": 7200,
        "startup_timeout": _STARTUP_TIMEOUT_SECONDS,
        "scaledown_window": _SCALEDOWN_WINDOW_SECONDS,
        "max_containers": _MAX_CONTAINERS,
        "buffer_containers": _BUFFER_CONTAINERS,
        "volumes": {
            MODEL_CACHE_DIR: cache_volume,
            AUDIO_STAGING_DIR: audio_staging_volume,
        },
    }
    if ENABLE_MEMORY_SNAPSHOT:
        kwargs["enable_memory_snapshot"] = True
    if ENABLE_GPU_SNAPSHOT:
        kwargs["experimental_options"] = {"enable_gpu_snapshot": True}
    return kwargs


def _preload_snapshot_backend(backend: Any, *, gpu_snapshot: bool) -> None:
    """Load Parakeet on the target device and optionally warm CUDA before snapshot."""
    if gpu_snapshot:
        backend.preload_model(DEFAULT_PARAKEET_MODEL, device="cuda")
        backend.warmup_transcription(
            duration_seconds=1.0,
            model_name=DEFAULT_PARAKEET_MODEL,
        )
        return

    backend.preload_model(DEFAULT_PARAKEET_MODEL, device="cpu")


def _restore_snapshot_backend(backend: Any, *, gpu_snapshot: bool) -> None:
    """Run post-restore setup that is not captured in the memory snapshot."""
    if gpu_snapshot:
        LOGGER.debug("GPU snapshot restore complete")
        return

    backend.move_models_to_device("cuda")
    backend.warmup_transcription(
        duration_seconds=1.0,
        model_name=DEFAULT_PARAKEET_MODEL,
    )
    LOGGER.debug("CPU snapshot restore: models moved to CUDA")


def _run_transcription_job(
    backend: Any,
    job: TranscriptionJob,
) -> TranscriptionJobResult:
    """Execute one transcription job using a preloaded backend instance."""
    import time

    from inference_server.parakeet_backend import ParakeetBackend

    started = time.monotonic()
    modal_task_id = os.getenv("MODAL_TASK_ID")
    progress_dict = modal.Dict.from_name(
        f"tara-progress-{job.run_id}",
        create_if_missing=True,
    )
    progress_dict[job.file_index] = {
        "relative_path": job.relative_path,
        "progress_pct": 0.0,
        "status": "starting",
    }

    def on_chunk_progress(chunk_index: int, total_chunks: int) -> None:
        progress_dict[job.file_index] = {
            "relative_path": job.relative_path,
            "progress_pct": ((chunk_index + 1) / total_chunks) * 100.0,
            "status": "running",
        }

    if (
        not _STAGING_PATH.fullmatch(job.storage_path)
        or not job.storage_path.startswith(f"runs/{job.run_id}/")
        or not isinstance(job.size_bytes, int)
        or isinstance(job.size_bytes, bool)
        or not 0 < job.size_bytes <= DEFAULT_MAX_MODAL_AUDIO_BYTES
        or not _SHA256.fullmatch(job.sha256_hex)
    ):
        raise ValueError("invalid audio reference")
    if not job.expires_at.endswith("Z"):
        raise ValueError("expired audio reference")
    expires_at = datetime.fromisoformat(job.expires_at.replace("Z", "+00:00"))
    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() != UTC.utcoffset(expires_at)
        or expires_at <= datetime.now(UTC)
    ):
        raise ValueError("expired audio reference")
    root = Path(AUDIO_STAGING_DIR).resolve()
    temp_path = root / job.storage_path
    try:
        resolved = temp_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("invalid audio reference") from exc
    if (
        temp_path.is_symlink()
        or not resolved.is_relative_to(root)
        or not temp_path.is_file()
        or temp_path.stat().st_size != job.size_bytes
    ):
        raise ValueError("invalid audio reference")
    digest = hashlib.sha256()
    with temp_path.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
    if digest.hexdigest() != job.sha256_hex:
        raise ValueError("invalid audio reference")

    if isinstance(backend, ParakeetBackend):
        response = backend.transcribe(
            resolved,
            model=job.model,
            language=job.language,
            chunk_progress_callback=on_chunk_progress,
        )
    else:
        response = backend.transcribe(
            resolved,
            model=job.model,
            language=job.language,
        )
    payload = response.model_dump()
    _validate_response_payload(payload)
    progress_dict[job.file_index] = {
        "relative_path": job.relative_path,
        "progress_pct": 100.0,
        "status": "complete",
    }
    return TranscriptionJobResult(
        file_index=job.file_index,
        relative_path=job.relative_path,
        payload=payload,
        modal_task_id=modal_task_id,
        processing_time_seconds=time.monotonic() - started,
    )


def _validate_response_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("transcription response is invalid")
    try:
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("transcription response is invalid") from exc
    if len(encoded) > _MAX_RESULT_BYTES:
        raise ValueError("transcription response exceeds the configured limit")
    segments = payload.get("segments", [])
    if not isinstance(segments, list) or len(segments) > _MAX_RESULT_SEGMENTS:
        raise ValueError("transcription response exceeds the configured limit")
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("transcription response is invalid")
        text = segment.get("text", "")
        if not isinstance(text, str) or len(text) > _MAX_SEGMENT_TEXT_CHARS:
            raise ValueError("transcription response exceeds the configured limit")


@app.cls(**_parakeet_transcriber_cls_kwargs())
class ParakeetTranscriber:
    """GPU Parakeet transcriber with Modal memory snapshot lifecycle hooks."""

    @modal.enter(snap=True)
    def load_model_for_snapshot(self) -> None:
        """Load Parakeet weights and warm CUDA before GPU memory snapshot."""
        import time

        from inference_server.parakeet_backend import ParakeetBackend

        started = time.monotonic()
        self._backend = ParakeetBackend()
        _preload_snapshot_backend(self._backend, gpu_snapshot=ENABLE_GPU_SNAPSHOT)
        LOGGER.info(
            "Parakeet snapshot preload finished in %.1fs (gpu_snapshot=%s)",
            time.monotonic() - started,
            ENABLE_GPU_SNAPSHOT,
        )

    @modal.enter(snap=False)
    def setup_runtime(self) -> None:
        """Light per-restore setup after memory snapshot restore."""
        _restore_snapshot_backend(self._backend, gpu_snapshot=ENABLE_GPU_SNAPSHOT)

    @modal.method()
    def transcribe_one(self, job: TranscriptionJob) -> TranscriptionJobResult:
        """Transcribe one audio file on a dedicated GPU container."""
        return _run_transcription_job(self._backend, job)


@app.function(
    image=image,
    gpu=["L4", "A10"],
    timeout=7200,
    startup_timeout=1200,
    scaledown_window=_SCALEDOWN_WINDOW_SECONDS,
    max_containers=_FASTAPI_MAX_CONTAINERS,
    volumes={MODEL_CACHE_DIR: cache_volume},
)
@modal.asgi_app(requires_proxy_auth=True)
def fastapi_app() -> Any:
    """Return the OpenAI-compatible FastAPI app (dev / legacy HTTP only).

    Use ``modal serve modal_inference.py`` for manual HTTP smoke tests.
    Production ``--modal`` runs use ``transcribe_one`` via ``Function.spawn()``.
    """
    from inference_server.app import app as inference_app

    return inference_app


@app.local_entrypoint()
def smoke_test(audio_path: str = "sample.wav") -> None:
    """Run one ``transcribe_one`` job remotely for manual verification."""
    sample = Path(audio_path)
    if not sample.is_file():
        raise FileNotFoundError(f"Audio file not found: {sample}")
    if sample.stat().st_size > DEFAULT_MAX_MODAL_AUDIO_BYTES:
        raise ValueError("Audio file exceeds the staging limit.")
    run_id = uuid.uuid4().hex
    remote_path = f"runs/{run_id}/{uuid.uuid4().hex}{sample.suffix.lower()}"
    digest = hashlib.sha256()
    with sample.open("rb") as source:
        while chunk := source.read(65_536):
            digest.update(chunk)
    with audio_staging_volume.batch_upload(force=False) as batch:
        batch.put_file(str(sample), remote_path)
    try:
        job = TranscriptionJob(
            run_id=run_id,
            file_index=1,
            relative_path=sample.name,
            storage_path=remote_path,
            size_bytes=sample.stat().st_size,
            sha256_hex=digest.hexdigest(),
            expires_at=(datetime.now(UTC) + timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z"),
            model=DEFAULT_PARAKEET_MODEL_WITH_PREFIX,
            language="fr",
        )
        print(ParakeetTranscriber().transcribe_one.remote(job))
    finally:
        audio_staging_volume.remove_file(f"runs/{run_id}", recursive=True)
