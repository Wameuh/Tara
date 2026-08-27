"""FastAPI application exposing an OpenAI-compatible transcription endpoint."""

from __future__ import annotations

import asyncio
import hmac
import logging
import multiprocessing as mp
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import BoundedSemaphore
from typing import Annotated, BinaryIO

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from inference_server.backend import BackendError, TranscriptionBackend, create_backend
from inference_server.ipc import (
    MAX_INFERENCE_IPC_BYTES,
    InferenceIpcViolation,
    decode_worker_message,
)
from inference_server.worker import run_transcription_worker

_DEFAULT_MODELS = frozenset(
    {
        "large-v3",
        "medium",
        "small",
        "base",
        "tiny",
        "parakeet:nvidia/parakeet-tdt-0.6b-v3",
    }
)
_AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"})
_COPY_CHUNK_BYTES = 1024 * 1024
_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def _positive_env_int(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} is outside the supported range")
    return value


_REQUEST_SLOTS = BoundedSemaphore(
    _positive_env_int("INFERENCE_MAX_CONCURRENT_REQUESTS", 2, 64)
)


def _configure_logging() -> logging.Logger:
    level_name = os.getenv("INFERENCE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    logger = logging.getLogger(__name__)
    logger.setLevel(level)
    return logger


LOGGER = _configure_logging()


class InferenceAdmissionMiddleware:
    """Authenticate and reserve capacity before multipart parsing starts."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v1/audio/transcriptions"
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        try:
            _require_bearer_token(headers.get("authorization"))
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )(scope, receive, send)
            return
        raw_length = headers.get("content-length")
        maximum = _positive_env_int(
            "INFERENCE_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024, 10**12
        )
        if raw_length is None or not raw_length.isascii() or not raw_length.isdecimal():
            await JSONResponse(
                {"detail": "A valid Content-Length header is required."},
                status_code=status.HTTP_411_LENGTH_REQUIRED,
            )(scope, receive, send)
            return
        if int(raw_length) > maximum + _MULTIPART_OVERHEAD_BYTES:
            await JSONResponse(
                {"detail": "Audio upload exceeds the configured limit."},
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )(scope, receive, send)
            return
        if not _REQUEST_SLOTS.acquire(blocking=False):
            await JSONResponse(
                {"detail": "Inference capacity is temporarily exhausted."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": "1"},
            )(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            _REQUEST_SLOTS.release()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan events (startup and shutdown)."""
    # Startup
    LOGGER.info(
        "Startup: disable_release=%s force_release=%s platform=%s",
        os.getenv("INFERENCE_DISABLE_RELEASE", "0"),
        os.getenv("INFERENCE_FORCE_RELEASE", "0"),
        os.name,
    )
    yield
    # Shutdown
    await _stop_heartbeat()


app = FastAPI(
    title="Transcription Inference Server",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(InferenceAdmissionMiddleware)

_HEARTBEAT_TASK: asyncio.Task[None] | None = None


def get_backend() -> TranscriptionBackend:
    """Return a backend instance (deprecated - use create_backend instead).

    This function is kept for backward compatibility but should not be used
    for new code. Use create_backend(model) instead.
    """
    from inference_server.backend import _get_faster_whisper_backend

    return _get_faster_whisper_backend()


@app.get("/health")
async def health() -> dict[str, str]:
    """Health probe endpoint."""

    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()],
    language: Annotated[str | None, Form()] = None,
    response_format: Annotated[str, Form()] = "json",
    stream: Annotated[bool, Form()] = False,
) -> Response:
    """OpenAI-compatible transcription endpoint."""

    if model not in _allowed_models():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported transcription model.",
        )
    if response_format != "json":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only response_format=json is supported.",
        )
    request_id = str(uuid.uuid4())
    LOGGER.info(
        "transcribe request id=%s model=%s language=%s", request_id, model, language
    )

    temp_path: Path | None = None
    use_worker = _use_worker_mode()
    response_owns_cleanup = False
    try:
        with NamedTemporaryFile(
            delete=False, suffix=_safe_audio_suffix(file.filename)
        ) as temp:
            temp_path = Path(temp.name)
            _copy_upload_limited(
                file.file,
                temp,
                _positive_env_int(
                    "INFERENCE_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024, 10**12
                ),
            )
        if stream:
            LOGGER.info(
                "transcribe stream start id=%s model=%s language=%s worker=%s",
                request_id,
                model,
                language,
                use_worker,
            )
            background_tasks.add_task(_cleanup_temp_file, temp_path)
            if use_worker:
                response = _stream_transcription_worker(
                    audio_path=temp_path,
                    model=model,
                    language=language,
                    background_tasks=background_tasks,
                )
            else:
                # Direct mode is an explicit compatibility opt-out from worker
                # isolation; only it constructs model code in the API process.
                backend = create_backend(model)
                response = _stream_transcription(
                    backend=backend,
                    audio_path=temp_path,
                    model=model,
                    language=language,
                    background_tasks=background_tasks,
                )
            response_owns_cleanup = True
            return response
        LOGGER.info(
            "transcribe start id=%s model=%s language=%s worker=%s",
            request_id,
            model,
            language,
            use_worker,
        )
        background_tasks.add_task(_cleanup_temp_file, temp_path)
        if use_worker:
            payload = _transcribe_via_worker(
                audio_path=temp_path,
                model=model,
                language=language,
            )
            LOGGER.info("transcribe end id=%s model=%s (worker)", request_id, model)
            response_owns_cleanup = True
            return JSONResponse(payload, background=background_tasks)
        backend = create_backend(model)
        result = backend.transcribe(temp_path, model=model, language=language)
        background_tasks.add_task(_safe_release_backend, backend)
        LOGGER.info("transcribe end id=%s model=%s", request_id, model)
        response_payload = result.model_dump()
        response_owns_cleanup = True
        return JSONResponse(response_payload, background=background_tasks)
    except BackendError as exc:
        LOGGER.error("Backend error id=%s: %s", request_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Unexpected error id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc
    finally:
        if not response_owns_cleanup:
            if temp_path is not None:
                _cleanup_temp_file(temp_path)


def _require_bearer_token(authorization: str | None) -> None:
    expected = _secret_from_env_or_file(
        "INFERENCE_BEARER_TOKEN", "INFERENCE_BEARER_TOKEN_FILE"
    )
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inference authentication is not configured.",
        )
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not supplied
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid inference credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _secret_from_env_or_file(value_name: str, file_name: str) -> str | None:
    path = os.getenv(file_name)
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Inference authentication is unavailable.",
            ) from exc
        return value or None
    value = os.getenv(value_name)
    return value.strip() if value and value.strip() else None


def _allowed_models() -> frozenset[str]:
    configured = os.getenv("INFERENCE_ALLOWED_MODELS")
    if configured is None:
        return _DEFAULT_MODELS
    values = frozenset(item.strip() for item in configured.split(",") if item.strip())
    return values


def _safe_audio_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in _AUDIO_SUFFIXES else ".bin"


def _copy_upload_limited(source: BinaryIO, target: BinaryIO, maximum: int) -> None:
    copied = 0
    while chunk := source.read(_COPY_CHUNK_BYTES):
        copied += len(chunk)
        if copied > maximum:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Audio upload exceeds the configured limit.",
            )
        target.write(chunk)


def _stream_transcription(
    *,
    backend: TranscriptionBackend,
    audio_path: Path,
    model: str,
    language: str | None,
    background_tasks: BackgroundTasks,
) -> StreamingResponse:
    """Return a StreamingResponse with SSE events for progressive transcription."""

    try:
        segments_iterable, info = backend.stream_transcribe(
            audio_path=audio_path,
            model=model,
            language=language,
        )
    except BackendError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    background_tasks.add_task(_safe_release_backend, backend)

    def event_stream() -> Iterable[bytes]:
        full_text: list[str] = []
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        for segment in segments_iterable:
            text = str(getattr(segment, "text", "")).strip()
            if not text:
                continue
            start = float(getattr(segment, "start", 0.0) or 0.0)
            end = float(getattr(segment, "end", 0.0) or 0.0)
            full_text.append(text)
            progress = None
            if duration > 0:
                progress = min(100.0, max(0.0, (end / duration) * 100.0))
            payload = {
                "type": "segment",
                "text": text,
                "start": start,
                "end": end,
                "progress": progress,
            }
            yield _sse(payload)

        payload = {
            "type": "final",
            "text": " ".join(full_text).strip(),
            "language": getattr(info, "language", language),
            "duration": duration or None,
            "model": model,
        }
        yield _sse(payload)
        LOGGER.info("stream final emitted model=%s duration=%s", model, duration)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        background=background_tasks,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(payload: dict[str, object]) -> bytes:
    """Serialize a dict as an SSE 'data:' event."""

    import json

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _safe_release_backend(backend: TranscriptionBackend) -> None:
    """Release backend resources with safety guards to avoid crashing the server."""

    disable_release = os.getenv("INFERENCE_DISABLE_RELEASE") == "1"
    force_release = os.getenv("INFERENCE_FORCE_RELEASE") == "1"
    if disable_release and not force_release:
        LOGGER.info("Skipping backend release due to INFERENCE_DISABLE_RELEASE=1")
        return
    if os.name == "nt" and not force_release and not disable_release:
        LOGGER.info(
            "Skipping backend release by default on Windows; "
            "set INFERENCE_FORCE_RELEASE=1 to enable"
        )
        return
    try:
        backend.release_all()
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Backend release failed (ignored): %s", exc)


def _use_worker_mode() -> bool:
    """Decide whether to run transcription in a worker subprocess."""

    env_value = os.getenv("INFERENCE_USE_WORKER")
    if env_value is not None:
        return env_value == "1"
    # Keep model execution outside the authenticated API process on every platform.
    return True


def _cleanup_temp_file(path: Path) -> None:
    """Delete a temporary audio file best-effort."""

    try:
        path.unlink(missing_ok=True)
    except Exception:  # pragma: no cover - best effort
        LOGGER.debug("Unable to delete temp file %s", path, exc_info=True)


def _spawn_worker(
    *,
    audio_path: Path,
    model: str,
    language: str | None,
    stream: bool,
) -> tuple[mp.Process, mp.connection.Connection]:
    """Spawn the transcription worker process."""

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=run_transcription_worker,
        args=(
            child_conn,
            {
                "audio_path": str(audio_path),
                "model": model,
                "language": language,
                "stream": stream,
            },
        ),
    )
    proc.start()
    child_conn.close()
    return proc, parent_conn


def _get_worker_timeout_seconds() -> float:
    """Return worker timeout from env or default."""

    raw = os.getenv("INFERENCE_WORKER_TIMEOUT_SECONDS")
    if raw is None:
        return 900.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        LOGGER.warning(
            "Invalid INFERENCE_WORKER_TIMEOUT_SECONDS=%s, using default", raw
        )
        return 900.0


def _transcribe_via_worker(
    *,
    audio_path: Path,
    model: str,
    language: str | None,
    timeout: float | None = None,
) -> dict[str, object]:
    """Execute a transcription in a worker process and return the payload."""

    proc, conn = _spawn_worker(
        audio_path=audio_path, model=model, language=language, stream=False
    )
    worker_timeout = timeout if timeout is not None else _get_worker_timeout_seconds()
    deadline = time.time() + worker_timeout
    payload: dict[str, object] | None = None
    error: str | None = None
    try:
        while True:
            if conn.poll(0.1):
                try:
                    msg = decode_worker_message(
                        conn.recv_bytes(MAX_INFERENCE_IPC_BYTES)
                    )
                except (EOFError, OSError, InferenceIpcViolation):
                    error = "Worker returned an invalid response"
                    break
                msg_type = msg.get("type")
                if msg_type == "final":
                    payload = msg.get("payload")
                    break
                if msg_type == "error":
                    error = msg.get("message", "Worker failed")
                    break
            if time.time() > deadline:
                error = "Worker timeout"
                break
            if not proc.is_alive() and not conn.poll():
                error = "Worker exited unexpectedly"
                break
        if error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error,
            )
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Worker returned no result",
            )
        return payload
    finally:
        conn.close()
        if proc.is_alive():
            proc.terminate()
        proc.join(timeout=1)


def _stream_transcription_worker(
    *,
    audio_path: Path,
    model: str,
    language: str | None,
    background_tasks: BackgroundTasks,
    timeout: float | None = None,
) -> StreamingResponse:
    """Stream transcription results from a worker process via SSE."""

    proc, conn = _spawn_worker(
        audio_path=audio_path, model=model, language=language, stream=True
    )
    worker_timeout = timeout if timeout is not None else _get_worker_timeout_seconds()
    deadline = time.monotonic() + worker_timeout

    def event_stream() -> Iterable[bytes]:
        try:
            while True:
                if conn.poll(0.1):
                    try:
                        msg = decode_worker_message(
                            conn.recv_bytes(MAX_INFERENCE_IPC_BYTES)
                        )
                    except (EOFError, OSError, InferenceIpcViolation):
                        yield _sse(
                            {
                                "type": "error",
                                "message": "Worker returned an invalid response",
                            }
                        )
                        break
                    msg_type = msg.get("type")
                    if msg_type == "segment":
                        yield _sse(msg)
                    elif msg_type == "final":
                        yield _sse(msg)
                        break
                    elif msg_type == "error":
                        # Send error as SSE event instead of raising HTTPException
                        # This prevents "response already started" errors
                        error_payload = {
                            "type": "error",
                            "message": msg.get("message", "Worker failed"),
                        }
                        yield _sse(error_payload)
                        break
                if not proc.is_alive() and not conn.poll():
                    # Send error as SSE event instead of raising HTTPException
                    error_payload = {
                        "type": "error",
                        "message": "Worker exited unexpectedly",
                    }
                    yield _sse(error_payload)
                    break
                if time.monotonic() > deadline:
                    yield _sse({"type": "error", "message": "Worker timeout"})
                    break
        finally:
            conn.close()
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        background=background_tasks,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.middleware("http")
async def _log_requests(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log each request with duration and status."""

    start = time.time()
    try:
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        LOGGER.info(
            "HTTP %s %s -> %s in %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
    except Exception:
        duration_ms = (time.time() - start) * 1000
        LOGGER.exception(
            "Unhandled error for %s %s after %.1fms",
            request.method,
            request.url.path,
            duration_ms,
        )
        raise


async def _start_heartbeat() -> None:
    """Heartbeat disabled (noop)."""
    return None


async def _stop_heartbeat() -> None:
    """Heartbeat disabled (noop)."""
    return None


__all__ = ["app", "get_backend"]
