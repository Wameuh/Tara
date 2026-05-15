"""FastAPI application exposing an OpenAI-compatible transcription endpoint."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import AsyncIterator, Iterable

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from inference_server.backend import BackendError, TranscriptionBackend, create_backend
from inference_server.models import TranscriptionResponse
from inference_server.worker import run_transcription_worker


def _configure_logging() -> logging.Logger:
    level_name = os.getenv("INFERENCE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    logger = logging.getLogger(__name__)
    logger.setLevel(level)
    return logger


LOGGER = _configure_logging()


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
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
    response_format: str = Form("json"),
    stream: bool = Form(False),
) -> JSONResponse:
    """OpenAI-compatible transcription endpoint."""

    request_id = str(uuid.uuid4())
    LOGGER.info("transcribe request id=%s model=%s language=%s", request_id, model, language)

    if response_format != "json":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only response_format=json is supported.",
        )

    # Select appropriate backend based on model name
    backend = create_backend(model)

    temp_path: Path | None = None
    use_worker = _use_worker_mode()
    try:
        with NamedTemporaryFile(delete=False, suffix=Path(file.filename or "audio").suffix) as temp:
            temp_path = Path(temp.name)
            shutil.copyfileobj(file.file, temp)
        if stream:
            LOGGER.info("transcribe stream start id=%s model=%s language=%s worker=%s", request_id, model, language, use_worker)
            background_tasks.add_task(_cleanup_temp_file, temp_path)
            if use_worker:
                return _stream_transcription_worker(
                    audio_path=temp_path,
                    model=model,
                    language=language,
                )
            return _stream_transcription(
                backend=backend,
                audio_path=temp_path,
                model=model,
                language=language,
                background_tasks=background_tasks,
            )
        LOGGER.info("transcribe start id=%s model=%s language=%s worker=%s", request_id, model, language, use_worker)
        background_tasks.add_task(_cleanup_temp_file, temp_path)
        if use_worker:
            payload = _transcribe_via_worker(
                audio_path=temp_path,
                model=model,
                language=language,
            )
            LOGGER.info("transcribe end id=%s model=%s (worker)", request_id, model)
            return JSONResponse(payload)
        result = backend.transcribe(temp_path, model=model, language=language)
        background_tasks.add_task(_safe_release_backend, backend)
        LOGGER.info("transcribe end id=%s model=%s", request_id, model)
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

    response_payload = result.model_dump()
    return JSONResponse(response_payload)


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

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _safe_release_backend(backend: TranscriptionBackend) -> None:
    """Release backend resources with safety guards to avoid crashing the server."""

    disable_release = os.getenv("INFERENCE_DISABLE_RELEASE") == "1"
    force_release = os.getenv("INFERENCE_FORCE_RELEASE") == "1"
    if disable_release and not force_release:
        LOGGER.info("Skipping backend release due to INFERENCE_DISABLE_RELEASE=1")
        return
    if os.name == "nt" and not force_release and not disable_release:
        LOGGER.info("Skipping backend release by default on Windows; set INFERENCE_FORCE_RELEASE=1 to enable")
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
    # Default to worker mode on Windows to isolate crashes and free VRAM on exit.
    return os.name == "nt"


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
    parent_conn, child_conn = ctx.Pipe()
    proc = ctx.Process(
        target=run_transcription_worker,
        args=(child_conn, {"audio_path": str(audio_path), "model": model, "language": language, "stream": stream}),
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
        LOGGER.warning("Invalid INFERENCE_WORKER_TIMEOUT_SECONDS=%s, using default", raw)
        return 900.0


def _transcribe_via_worker(
    *,
    audio_path: Path,
    model: str,
    language: str | None,
    timeout: float | None = None,
) -> dict[str, object]:
    """Execute a transcription in a worker process and return the payload."""

    proc, conn = _spawn_worker(audio_path=audio_path, model=model, language=language, stream=False)
    worker_timeout = timeout if timeout is not None else _get_worker_timeout_seconds()
    deadline = time.time() + worker_timeout
    payload: dict[str, object] | None = None
    error: str | None = None
    try:
        while True:
            if conn.poll(0.1):
                msg = conn.recv()
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
) -> StreamingResponse:
    """Stream transcription results from a worker process via SSE."""

    proc, conn = _spawn_worker(audio_path=audio_path, model=model, language=language, stream=True)

    def event_stream() -> Iterable[bytes]:
        try:
            while True:
                if conn.poll(0.1):
                    msg = conn.recv()
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
        finally:
            conn.close()
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.middleware("http")
async def _log_requests(request, call_next):
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
        LOGGER.exception("Unhandled error for %s %s after %.1fms", request.method, request.url.path, duration_ms)
        raise




async def _start_heartbeat() -> None:
    """Heartbeat disabled (noop)."""
    return None


async def _stop_heartbeat() -> None:
    """Heartbeat disabled (noop)."""
    return None


__all__ = ["app", "get_backend"]

