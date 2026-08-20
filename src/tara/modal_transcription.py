"""Modal Function.spawn() transcription client for parallel GPU inference."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from tara.config import TaraConfig
from tara.modal_lifecycle import list_inference_container_ids
from tara.providers.costing import CostSnapshot, convert_native_cost
from tara.providers.events import UsageAttempt
from tara.transcription import (
    TranscriptionDirectoryResult,
    TranscriptionFileResult,
    TranscriptionResponse,
    _response_from_mapping,
    discover_audio_files,
)
from tara.yaml_utils import write_yaml

LOGGER = logging.getLogger(__name__)

MODAL_APP_NAME = "tara-parakeet-inference"
MODAL_CLASS_NAME = "ParakeetTranscriber"
MODAL_METHOD_NAME = "transcribe_one"
MAX_MODAL_TRANSCRIPTION_RETRIES = 1
_PROGRESS_DICT_PREFIX = "tara-progress-"
MODAL_POLL_CHUNK_SECONDS = 55.0
MODAL_QUEUE_LOG_INTERVAL_SECONDS = 30.0
_STAGING_VOLUME_NAME = "tara-audio-staging"
_MAX_RESULT_BYTES = 32 * 1024 * 1024
_MAX_RESULT_SEGMENTS = 200_000
_MAX_SEGMENT_TEXT_CHARS = 1_000_000

CancellationCheck = Callable[[], None]
ProgressCallback = Callable[[int, int, float], None]
UsageAttemptCallback = Callable[[UsageAttempt], None]


@dataclass(slots=True)
class _ActiveModalCall:
    job: Any
    call: Any
    storage_path: str
    attempt_id: str
    started_at: datetime
    started_monotonic: float
    attempt_number: int
    processing_started_at: float | None = None


def _repo_root() -> Path:
    """Return the TaraRepo root directory."""
    return Path(__file__).resolve().parents[2]


def _import_modal_job_types() -> tuple[type[Any], type[Any]]:
    """Import job dataclasses from the Modal deployment module."""
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from modal_inference import TranscriptionJob, TranscriptionJobResult

    return TranscriptionJob, TranscriptionJobResult


def _get_transcribe_spawn_target() -> Any:
    """Resolve the deployed ``ParakeetTranscriber.transcribe_one`` spawn target."""
    import modal

    transcriber_cls = modal.Cls.from_name(MODAL_APP_NAME, MODAL_CLASS_NAME)
    method = getattr(transcriber_cls(), MODAL_METHOD_NAME, None)
    if method is None or not hasattr(method, "spawn"):
        message = (
            f"Deployed Modal class '{MODAL_APP_NAME}.{MODAL_CLASS_NAME}' "
            f"has no spawnable method '{MODAL_METHOD_NAME}'."
        )
        raise RuntimeError(message)
    return method


def _upload_audio_reference(
    volume: Any, path: Path, run_id: str, *, max_bytes: int
) -> tuple[str, int, str]:
    """Stage exactly one file by reference; never materialise its bytes in RAM."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("audio staging source is invalid")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError("audio staging source exceeds the configured limit")
    token = uuid.uuid4().hex
    extension = path.suffix.lower().lstrip(".")
    remote = f"runs/{run_id}/{token}.{extension}"
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(65_536):
            digest.update(chunk)
    with volume.batch_upload(force=False) as batch:
        batch.put_file(str(path), remote)
    if path.stat().st_size != size:
        raise ValueError("audio staging source changed during upload")
    return remote, size, digest.hexdigest()


def _safe_remove_staged(volume: Any, path: str, *, recursive: bool = False) -> None:
    try:
        volume.remove_file(path, recursive=recursive)
    except Exception:
        LOGGER.warning("Unable to remove a Modal staging reference", exc_info=True)


def _cancel_call(call: Any) -> None:
    cancel = getattr(call, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            LOGGER.debug("Unable to cancel a Modal call", exc_info=True)


def _emit_usage(
    active: _ActiveModalCall,
    status: str,
    callback: UsageAttemptCallback | None,
    *,
    config: TaraConfig | None = None,
    processing_seconds: float | None = None,
) -> None:
    if callback is None:
        return
    finished = datetime.now(UTC)
    cost = _modal_cost_snapshot(active, config, processing_seconds)
    callback(
        UsageAttempt(
            attempt_id=active.attempt_id,
            operation_family="transcription",
            provider="modal",
            model=str(active.job.model),
            status=status,
            started_at=active.started_at.isoformat().replace("+00:00", "Z"),
            finished_at=finished.isoformat().replace("+00:00", "Z"),
            duration_ms=max(
                0, int((time.monotonic() - active.started_monotonic) * 1_000)
            ),
            cost_micro_eur=cost.cost_micro_eur if cost else None,
            cost_source=cost.source if cost else "unavailable",
            native_cost_micros=cost.native_cost_micros if cost else None,
            native_currency=cost.native_currency if cost else None,
            conversion_rate=cost.conversion_rate if cost else None,
        )
    )


def _modal_cost_snapshot(
    active: _ActiveModalCall,
    config: TaraConfig | None,
    processing_seconds: float | None,
) -> CostSnapshot | None:
    """Estimate one attempt from its own processing duration and price snapshot."""
    if config is None:
        return None
    native_rate = config.transcription.modal_usd_per_second
    eur_rate = config.analysis.llm.usd_to_eur_rate
    if native_rate is None or eur_rate is None:
        return None
    if processing_seconds is None:
        if active.processing_started_at is None:
            return None
        processing_seconds = max(0.0, time.monotonic() - active.processing_started_at)
    from decimal import Decimal, InvalidOperation

    try:
        native_cost = Decimal(native_rate) * Decimal(str(processing_seconds))
    except InvalidOperation as exc:
        raise ValueError("cost configuration is invalid") from exc
    return convert_native_cost(
        native_cost,
        native_currency="USD",
        eur_per_native=eur_rate,
        source="modal_duration_estimate",
    )


def _validate_result_payload(result: Any) -> None:
    payload = getattr(result, "payload", None)
    if not isinstance(payload, Mapping):
        raise ValueError("Modal transcription result is invalid")
    try:
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Modal transcription result is invalid") from exc
    if len(encoded) > _MAX_RESULT_BYTES:
        raise ValueError("Modal transcription result exceeds the configured limit")
    segments = payload.get("segments", [])
    if not isinstance(segments, list) or len(segments) > _MAX_RESULT_SEGMENTS:
        raise ValueError("Modal transcription result exceeds the configured limit")
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise ValueError("Modal transcription result is invalid")
        text = segment.get("text", "")
        if not isinstance(text, str) or len(text) > _MAX_SEGMENT_TEXT_CHARS:
            raise ValueError("Modal transcription result exceeds the configured limit")
    relative = getattr(result, "relative_path", None)
    if not isinstance(relative, str):
        raise ValueError("Modal transcription result is invalid")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("Modal transcription result is invalid")


def _resolve_modal_spawn_parallelism(
    total_files: int,
    configured_parallelism: int,
    *,
    max_containers: int,
    files_per_container: int,
) -> int:
    """Return the strict per-job submission window for Modal calls."""
    if total_files <= 1:
        return 1

    safe_files_per_container = max(1, files_per_container)
    safe_max_containers = max(1, max_containers)
    ratio_limit = (
        total_files + safe_files_per_container - 1
    ) // safe_files_per_container
    modal_cap = min(safe_max_containers, ratio_limit)

    if configured_parallelism <= 0:
        return modal_cap
    return min(configured_parallelism, modal_cap)


def _read_progress_state(
    progress_dict: Any,
    file_index: int,
) -> Mapping[str, object] | None:
    """Return the latest progress payload for one file, if available."""
    try:
        state = progress_dict[file_index]
    except Exception:
        return None
    return state if isinstance(state, Mapping) else None


def _job_is_processing(progress_state: Mapping[str, object] | None) -> bool:
    """Return whether a Modal job has started transcribing on a container."""
    if progress_state is None:
        return False
    status = str(progress_state.get("status", ""))
    if status in {"running", "complete"}:
        return True
    try:
        return float(progress_state.get("progress_pct", 0.0)) > 0.0
    except (TypeError, ValueError):
        return False


def _job_wait_deadline(
    *,
    spawned_at: float,
    processing_started_at: float | None,
    queue_grace_seconds: int,
    request_timeout_seconds: int,
) -> float:
    """Return the monotonic deadline for queue wait or active processing."""
    if processing_started_at is None:
        return spawned_at + queue_grace_seconds
    return processing_started_at + request_timeout_seconds


def _raise_modal_job_timeout(
    *,
    relative_path: str,
    spawned_at: float,
    processing_started_at: float | None,
    queue_grace_seconds: int,
    request_timeout_seconds: int,
) -> None:
    """Raise when a Modal job exceeds its queue or processing deadline."""
    from tara.pipeline import TaraPipelineError

    if processing_started_at is None:
        elapsed = int(time.monotonic() - spawned_at)
        message = (
            f"Modal transcription timed out waiting in queue for {relative_path} "
            f"after {queue_grace_seconds}s ({elapsed}s elapsed). "
            "Increase transcription.modal_queue_grace_seconds if cold starts are slow."
        )
    else:
        processing_elapsed = int(time.monotonic() - processing_started_at)
        message = (
            f"Modal transcription timed out processing {relative_path} "
            f"after {request_timeout_seconds}s ({processing_elapsed}s elapsed). "
            "Increase transcription.request_timeout_seconds for long audio files."
        )
    raise TaraPipelineError(message)


def _wait_for_function_call(
    call: Any,
    *,
    relative_path: str,
    spawned_at: float,
    progress_dict: Any,
    file_index: int,
    queue_grace_seconds: int,
    request_timeout_seconds: int,
) -> Any:
    """Wait for one spawned Modal job, with separate queue and processing timeouts."""
    from modal.exception import OutputExpiredError

    processing_started_at: float | None = None
    last_queue_log_at = 0.0

    while True:
        now = time.monotonic()
        progress_state = _read_progress_state(progress_dict, file_index)
        if _job_is_processing(progress_state) and processing_started_at is None:
            processing_started_at = now
        deadline = _job_wait_deadline(
            spawned_at=spawned_at,
            processing_started_at=processing_started_at,
            queue_grace_seconds=queue_grace_seconds,
            request_timeout_seconds=request_timeout_seconds,
        )
        remaining = deadline - now
        if remaining <= 0:
            _raise_modal_job_timeout(
                relative_path=relative_path,
                spawned_at=spawned_at,
                processing_started_at=processing_started_at,
                queue_grace_seconds=queue_grace_seconds,
                request_timeout_seconds=request_timeout_seconds,
            )

        if (
            processing_started_at is None
            and now - last_queue_log_at >= MODAL_QUEUE_LOG_INTERVAL_SECONDS
        ):
            LOGGER.info(
                "Modal job still queued for %s (%ss waiting for a container)...",
                relative_path,
                int(now - spawned_at),
            )
            last_queue_log_at = now

        try:
            return call.get(timeout=min(remaining, MODAL_POLL_CHUNK_SECONDS))
        except TimeoutError:
            continue
        except OutputExpiredError as exc:
            from tara.pipeline import TaraPipelineError

            message = f"Modal transcription result expired for {relative_path}."
            raise TaraPipelineError(message) from exc


def _average_progress(
    states: Mapping[int, Mapping[str, object]],
    *,
    total_files: int,
) -> float:
    """Compute average progress across all files (missing files count as 0%)."""
    if total_files == 0:
        return 0.0
    total = 0.0
    for state in states.values():
        status = str(state.get("status", ""))
        if status == "complete":
            total += 100.0
        else:
            try:
                total += float(state.get("progress_pct", 0.0))
            except (TypeError, ValueError):
                total += 0.0
    missing = total_files - len(states)
    return total / total_files if missing <= 0 else total / total_files


def _progress_dict_name(run_id: str) -> str:
    """Return the run-scoped Modal Dict name for transcription progress."""
    return f"{_PROGRESS_DICT_PREFIX}{run_id}"


def _cleanup_progress_dict(run_id: str) -> None:
    """Delete the run-scoped progress dict after transcription completes."""
    import modal

    progress_dict = modal.Dict.from_name(
        _progress_dict_name(run_id), create_if_missing=False
    )
    try:
        progress_dict.clear()
    except Exception as exc:
        LOGGER.debug("Unable to clear Modal progress dict for run %s: %s", run_id, exc)
    try:
        modal.Dict.delete(_progress_dict_name(run_id))
    except Exception as exc:
        LOGGER.debug("Unable to delete Modal progress dict for run %s: %s", run_id, exc)


def _log_average_progress(
    progress_dict: Any,
    *,
    total_files: int,
    interval_seconds: float,
    last_log_at: float,
) -> float:
    """Log aggregated Modal progress when the poll interval has elapsed."""
    now = time.monotonic()
    if now - last_log_at < interval_seconds:
        return last_log_at
    try:
        states = {int(key): value for key, value in progress_dict.items()}
    except Exception:
        states = {}
    active = sum(
        1 for state in states.values() if str(state.get("status")) == "running"
    )
    done = sum(1 for state in states.values() if str(state.get("status")) == "complete")
    average = _average_progress(states, total_files=total_files)
    LOGGER.info(
        "Modal transcription progress: %.1f%% average "
        "(%s files, %s active, %s complete)",
        average,
        total_files,
        active,
        done,
    )
    return now


def _write_job_result(
    audio_dir: Path,
    config: TaraConfig,
    job_result: Any,
) -> Path:
    """Write one Modal transcription result to the local output directory."""
    output_dir = audio_dir / config.transcription.output_dir
    output_path = output_dir / Path(job_result.relative_path).with_suffix(".yaml")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(output_path, job_result.payload)
    return output_path


def _job_result_to_file_result(
    audio_dir: Path,
    output_path: Path,
    job_result: Any,
) -> TranscriptionFileResult:
    """Convert a Modal job result into a local file result record."""
    payload = job_result.payload
    response = (
        _response_from_mapping(payload, fallback_language=None)
        if isinstance(payload, Mapping)
        else TranscriptionResponse(text="", segments=[])
    )
    return TranscriptionFileResult(
        file_path=audio_dir / job_result.relative_path,
        output_path=output_path,
        duration_seconds=response.duration,
        processing_time_seconds=job_result.processing_time_seconds,
    )


def _spawn_job_with_retry(
    fn: Any,
    job: Any,
    *,
    relative_path: str,
    progress_dict: Any,
    queue_grace_seconds: int,
    request_timeout_seconds: int,
) -> Any:
    """Spawn one Modal job and retry once on failure."""
    last_error: Exception | None = None
    for attempt in range(MAX_MODAL_TRANSCRIPTION_RETRIES + 1):
        spawned_at = time.monotonic()
        call = fn.spawn(job)
        try:
            return _wait_for_function_call(
                call,
                relative_path=relative_path,
                spawned_at=spawned_at,
                progress_dict=progress_dict,
                file_index=job.file_index,
                queue_grace_seconds=queue_grace_seconds,
                request_timeout_seconds=request_timeout_seconds,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= MAX_MODAL_TRANSCRIPTION_RETRIES:
                break
            backoff = 2**attempt
            LOGGER.warning(
                "Modal transcription failed for %s (attempt %s/%s): %s; "
                "retrying in %ss.",
                relative_path,
                attempt + 1,
                MAX_MODAL_TRANSCRIPTION_RETRIES + 1,
                exc,
                backoff,
            )
            time.sleep(backoff)
    from tara.pipeline import TaraPipelineError

    message = f"Modal transcription failed for {relative_path}: {last_error}"
    raise TaraPipelineError(message) from last_error


def _poll_spawned_jobs(
    fn: Any,
    spawned: list[tuple[Any, Any]],
    *,
    total_files: int,
    progress_dict: Any,
    progress_interval_seconds: float,
    queue_grace_seconds: int,
    request_timeout_seconds: int,
) -> tuple[list[Any], set[str]]:
    """Poll spawned Modal jobs until every submitted job completes."""
    modal_task_ids: set[str] = set()
    completed_results: list[Any] = []
    pending = dict(spawned)
    last_log_at = 0.0
    spawned_at_by_job = {job: time.monotonic() for job, _ in spawned}
    processing_started_at_by_job: dict[Any, float | None] = dict.fromkeys(
        spawned_at_by_job,
        None,
    )
    last_queue_log_at_by_job = dict.fromkeys(spawned_at_by_job, 0.0)

    while pending:
        last_log_at = _log_average_progress(
            progress_dict,
            total_files=total_files,
            interval_seconds=progress_interval_seconds,
            last_log_at=last_log_at,
        )
        for job, call in list(pending.items()):
            now = time.monotonic()
            progress_state = _read_progress_state(progress_dict, job.file_index)
            if (
                _job_is_processing(progress_state)
                and processing_started_at_by_job[job] is None
            ):
                processing_started_at_by_job[job] = now
            processing_started_at = processing_started_at_by_job[job]
            deadline = _job_wait_deadline(
                spawned_at=spawned_at_by_job[job],
                processing_started_at=processing_started_at,
                queue_grace_seconds=queue_grace_seconds,
                request_timeout_seconds=request_timeout_seconds,
            )
            remaining = deadline - now
            if remaining <= 0:
                _raise_modal_job_timeout(
                    relative_path=job.relative_path,
                    spawned_at=spawned_at_by_job[job],
                    processing_started_at=processing_started_at,
                    queue_grace_seconds=queue_grace_seconds,
                    request_timeout_seconds=request_timeout_seconds,
                )

            if (
                processing_started_at is None
                and now - last_queue_log_at_by_job[job]
                >= MODAL_QUEUE_LOG_INTERVAL_SECONDS
            ):
                LOGGER.info(
                    "Modal job still queued for %s (%ss waiting for a container)...",
                    job.relative_path,
                    int(now - spawned_at_by_job[job]),
                )
                last_queue_log_at_by_job[job] = now

            try:
                job_result = call.get(timeout=0)
            except TimeoutError:
                continue
            except Exception:
                pending.pop(job)
                job_result = _spawn_job_with_retry(
                    fn,
                    job,
                    relative_path=job.relative_path,
                    progress_dict=progress_dict,
                    queue_grace_seconds=queue_grace_seconds,
                    request_timeout_seconds=request_timeout_seconds,
                )
            if job_result.modal_task_id:
                modal_task_ids.add(str(job_result.modal_task_id))
            completed_results.append(job_result)
            pending.pop(job, None)
            LOGGER.info(
                "Finished transcription %s/%s: %s (%.1fs on Modal)",
                job.file_index,
                total_files,
                job.relative_path,
                job_result.processing_time_seconds,
            )
        if pending:
            time.sleep(1.0)

    return completed_results, modal_task_ids


def transcribe_audio_directory_via_map(
    audio_dir: Path,
    config: TaraConfig,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    usage_attempt_callback: UsageAttemptCallback | None = None,
) -> TranscriptionDirectoryResult:
    """Transcribe audio files through a bounded, lazy Modal spawn window."""
    import modal

    transcription_job_cls, _ = _import_modal_job_types()
    fn = _get_transcribe_spawn_target()
    run_id = uuid.uuid4().hex
    run_start_container_ids = frozenset(list_inference_container_ids())
    audio_files = discover_audio_files(
        audio_dir=audio_dir,
        extensions=config.transcription.audio_extensions,
        recursive=config.transcription.recursive,
    )
    total_files = len(audio_files)
    if total_files == 0:
        return TranscriptionDirectoryResult(
            file_results=[],
            modal_container_ids=frozenset(),
            run_start_container_ids=run_start_container_ids,
        )
    parallelism = _resolve_modal_spawn_parallelism(
        total_files,
        config.transcription.parallelism,
        max_containers=config.transcription.modal_max_containers,
        files_per_container=config.transcription.modal_files_per_container,
    )
    progress_dict = modal.Dict.from_name(
        _progress_dict_name(run_id), create_if_missing=True
    )
    staging_volume = modal.Volume.from_name(
        _STAGING_VOLUME_NAME, create_if_missing=True
    )
    pending_paths = iter(enumerate(audio_files, start=1))
    active: dict[int, _ActiveModalCall] = {}
    modal_task_ids: set[str] = set()
    file_results: list[TranscriptionFileResult] = []
    last_progress_log = 0.0

    def check_cancelled() -> None:
        if cancellation_check is not None:
            cancellation_check()

    def spawn_attempt(
        job: Any, storage_path: str, attempt_number: int
    ) -> _ActiveModalCall:
        attempt = _ActiveModalCall(
            job,
            None,
            storage_path,
            "pa_" + secrets.token_urlsafe(18),
            datetime.now(UTC),
            time.monotonic(),
            attempt_number,
        )
        try:
            attempt.call = fn.spawn(job)
        except Exception:
            _emit_usage(
                attempt,
                "failed",
                usage_attempt_callback,
                config=config,
            )
            raise
        return attempt

    def start(file_index: int, audio_path: Path, attempt_number: int) -> None:
        check_cancelled()
        storage_path, size_bytes, sha256_hex = _upload_audio_reference(
            staging_volume,
            audio_path,
            run_id,
            max_bytes=config.transcription.modal_max_audio_bytes,
        )
        job = transcription_job_cls(
            run_id=run_id,
            file_index=file_index,
            relative_path=audio_path.relative_to(audio_dir).as_posix(),
            storage_path=storage_path,
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
            expires_at=(datetime.now(UTC) + timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z"),
            model=config.transcription.model,
            language=config.language,
        )
        while True:
            try:
                active[file_index] = spawn_attempt(job, storage_path, attempt_number)
                return
            except Exception:
                if attempt_number > MAX_MODAL_TRANSCRIPTION_RETRIES:
                    _safe_remove_staged(staging_volume, storage_path)
                    raise
                attempt_number += 1

    try:
        exhausted = False
        while active or not exhausted:
            check_cancelled()
            while not exhausted and len(active) < parallelism:
                try:
                    file_index, audio_path = next(pending_paths)
                except StopIteration:
                    exhausted = True
                    break
                start(file_index, audio_path, 1)

            last_progress_log = _log_average_progress(
                progress_dict,
                total_files=total_files,
                interval_seconds=config.transcription.modal_progress_interval_seconds,
                last_log_at=last_progress_log,
            )
            made_progress = False
            for file_index, current in list(active.items()):
                check_cancelled()
                state = _read_progress_state(progress_dict, file_index)
                if _job_is_processing(state) and current.processing_started_at is None:
                    current.processing_started_at = time.monotonic()
                if progress_callback is not None and state is not None:
                    try:
                        ratio = float(state.get("progress_pct", 0.0)) / 100.0
                    except (TypeError, ValueError):
                        ratio = 0.0
                    progress_callback(
                        file_index, total_files, min(1.0, max(0.0, ratio))
                    )
                deadline = _job_wait_deadline(
                    spawned_at=current.started_monotonic,
                    processing_started_at=current.processing_started_at,
                    queue_grace_seconds=config.transcription.modal_queue_grace_seconds,
                    request_timeout_seconds=config.transcription.request_timeout_seconds,
                )
                if time.monotonic() >= deadline:
                    _cancel_call(current.call)
                    _emit_usage(
                        current,
                        "timed_out",
                        usage_attempt_callback,
                        config=config,
                    )
                    active.pop(file_index)
                    _safe_remove_staged(staging_volume, current.storage_path)
                    raise TimeoutError("Modal transcription attempt timed out")
                try:
                    result = current.call.get(timeout=0)
                except TimeoutError:
                    continue
                except Exception as exc:
                    _emit_usage(
                        current,
                        "failed",
                        usage_attempt_callback,
                        config=config,
                    )
                    active.pop(file_index)
                    made_progress = True
                    if current.attempt_number <= MAX_MODAL_TRANSCRIPTION_RETRIES:
                        try:
                            active[file_index] = spawn_attempt(
                                current.job,
                                current.storage_path,
                                current.attempt_number + 1,
                            )
                        except Exception:
                            _safe_remove_staged(staging_volume, current.storage_path)
                            raise
                        continue
                    _safe_remove_staged(staging_volume, current.storage_path)
                    from tara.pipeline import TaraPipelineError

                    raise TaraPipelineError(
                        "Modal transcription failed after bounded retries"
                    ) from exc
                try:
                    _validate_result_payload(result)
                except ValueError:
                    active.pop(file_index)
                    _emit_usage(
                        current,
                        "failed",
                        usage_attempt_callback,
                        config=config,
                    )
                    _safe_remove_staged(staging_volume, current.storage_path)
                    raise
                active.pop(file_index)
                _emit_usage(
                    current,
                    "success",
                    usage_attempt_callback,
                    config=config,
                    processing_seconds=float(result.processing_time_seconds),
                )
                _safe_remove_staged(staging_volume, current.storage_path)
                if result.modal_task_id:
                    modal_task_ids.add(str(result.modal_task_id))
                output_path = _write_job_result(audio_dir, config, result)
                file_results.append(
                    _job_result_to_file_result(audio_dir, output_path, result)
                )
                if progress_callback is not None:
                    progress_callback(file_index, total_files, 1.0)
                made_progress = True
            if active and not made_progress:
                time.sleep(0.1)
    except BaseException:
        for current in active.values():
            _cancel_call(current.call)
            _emit_usage(
                current,
                "cancelled",
                usage_attempt_callback,
                config=config,
            )
        raise
    finally:
        for current in active.values():
            _safe_remove_staged(staging_volume, current.storage_path)
        _safe_remove_staged(staging_volume, f"runs/{run_id}", recursive=True)
        _cleanup_progress_dict(run_id)

    tracked_ids = frozenset(modal_task_ids)
    if tracked_ids:
        LOGGER.debug(
            "Modal transcription tracked %s container/task ID(s): %s",
            len(tracked_ids),
            ", ".join(sorted(tracked_ids)),
        )

    return TranscriptionDirectoryResult(
        file_results=sorted(file_results, key=lambda item: str(item.file_path)),
        modal_container_ids=tracked_ids,
        run_start_container_ids=run_start_container_ids,
    )
