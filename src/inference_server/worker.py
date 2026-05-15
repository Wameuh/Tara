"""Worker process entry point for running faster-whisper safely in isolation."""

from __future__ import annotations

import logging
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from inference_server.backend import TranscriptionBackend, create_backend

LOGGER = logging.getLogger(__name__)


def run_transcription_worker(conn: Connection, params: dict[str, Any]) -> None:
    """Execute a transcription in a dedicated process and stream events back.

    The worker sends dict messages over ``conn`` with ``type`` keys:

    - ``segment``: intermediate segment with timing and progress.
    - ``final``: final result (streaming final metadata or full payload).
    - ``error``: error string if the worker fails.
    """
    try:
        audio_path = Path(params["audio_path"])
        model = params["model"]
        language = params.get("language")
        stream = bool(params.get("stream"))

        # Create appropriate backend based on model name
        backend = create_backend(model)

        if stream:
            _stream_transcription(conn, backend, audio_path, model=model, language=language)
        else:
            _transcribe_once(conn, backend, audio_path, model=model, language=language)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.debug("Worker error", exc_info=True)
        try:
            conn.send({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _transcribe_once(
    conn: Connection,
    backend: TranscriptionBackend,
    audio_path: Path,
    *,
    model: str,
    language: str | None,
) -> None:
    """Run a single transcription and send the payload."""

    result = backend.transcribe(audio_path, model=model, language=language)
    conn.send({"type": "final", "payload": result.model_dump()})


def _stream_transcription(
    conn: Connection,
    backend: TranscriptionBackend,
    audio_path: Path,
    *,
    model: str,
    language: str | None,
) -> None:
    """Run a streaming transcription and forward segments."""

    segments_iterable, info = backend.stream_transcribe(
        audio_path=audio_path,
        model=model,
        language=language,
    )
    full_text: list[str] = []
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    resolved_language = getattr(info, "language", None) or language

    for segment in segments_iterable:
        text = str(getattr(segment, "text", "")).strip()
        if not text:
            continue
        start = float(getattr(segment, "start", 0.0) or 0.0)
        end = float(getattr(segment, "end", 0.0) or 0.0)
        progress = None
        if duration > 0:
            progress = min(100.0, max(0.0, (end / duration) * 100.0))
        full_text.append(text)
        conn.send(
            {
                "type": "segment",
                "text": text,
                "start": start,
                "end": end,
                "progress": progress,
            },
        )

    conn.send(
        {
            "type": "final",
            "text": " ".join(full_text).strip(),
            "language": resolved_language,
            "duration": duration or None,
            "model": model,
        },
    )

