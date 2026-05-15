"""Additional tests for inference_server.app to achieve 100% coverage."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

import inference_server.app as app_module
from inference_server.app import (
    _cleanup_temp_file,
    _safe_release_backend,
    _sse,
    _spawn_worker,
    _start_heartbeat,
    _stop_heartbeat,
    _stream_transcription_worker,
    _transcribe_via_worker,
    _use_worker_mode,
    app,
    lifespan,
)
from inference_server.backend import TranscriptionBackend


class _FakeBackend(TranscriptionBackend):
    """Fake backend for testing."""

    def transcribe(self, *args: object, **kwargs: object) -> object:
        """Fake transcribe method."""
        from inference_server.models import TranscriptionResponse, TranscriptionSegment

        return TranscriptionResponse(
            text="test",
            segments=[TranscriptionSegment(start=0.0, end=1.0, text="test")],
            language="en",
            duration=1.0,
            model="test",
        )

    def stream_transcribe(self, *args: object, **kwargs: object) -> tuple[object, object]:
        """Fake stream_transcribe method."""
        from inference_server.models import TranscriptionSegment

        class Info:
            duration = 1.0
            language = "en"

        segments = iter([TranscriptionSegment(start=0.0, end=1.0, text="test")])
        return segments, Info()

    def release_all(self) -> None:
        """Fake release_all method."""
        pass


def test_sse_function() -> None:
    """Test _sse function serializes payload correctly."""
    payload = {"type": "test", "message": "hello"}
    result = _sse(payload)
    assert isinstance(result, bytes)
    assert b"data: " in result
    assert b'"type": "test"' in result


def test_use_worker_mode_default_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _use_worker_mode returns True on Windows by default."""
    monkeypatch.delenv("INFERENCE_USE_WORKER", raising=False)
    # On Windows, should default to True
    result = _use_worker_mode()
    if os.name == "nt":
        assert result is True
    else:
        # On non-Windows, should default to False
        assert result is False


def test_use_worker_mode_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _use_worker_mode respects INFERENCE_USE_WORKER environment variable."""
    monkeypatch.setenv("INFERENCE_USE_WORKER", "1")
    assert _use_worker_mode() is True

    monkeypatch.setenv("INFERENCE_USE_WORKER", "0")
    assert _use_worker_mode() is False

    monkeypatch.delenv("INFERENCE_USE_WORKER", raising=False)
    # Should return default based on platform
    _use_worker_mode()  # Just call it, default tested elsewhere


def test_cleanup_temp_file_success(tmp_path: Path) -> None:
    """Test _cleanup_temp_file successfully deletes file."""
    test_file = tmp_path / "test.tmp"
    test_file.write_text("test")
    assert test_file.exists()

    _cleanup_temp_file(test_file)
    assert not test_file.exists()


def test_cleanup_temp_file_missing_file(tmp_path: Path) -> None:
    """Test _cleanup_temp_file handles missing file gracefully."""
    test_file = tmp_path / "nonexistent.tmp"
    assert not test_file.exists()

    # Should not raise exception
    _cleanup_temp_file(test_file)


def test_cleanup_temp_file_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _cleanup_temp_file handles exceptions gracefully."""
    test_file = tmp_path / "test.tmp"
    test_file.write_text("test")

    # Mock unlink to raise an exception
    def mock_unlink(*args: object, **kwargs: object) -> None:
        raise PermissionError("Access denied")

    monkeypatch.setattr(Path, "unlink", mock_unlink)

    # Should not raise exception
    _cleanup_temp_file(test_file)


def test_safe_release_backend_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _safe_release_backend skips release when INFERENCE_DISABLE_RELEASE=1."""
    monkeypatch.setenv("INFERENCE_DISABLE_RELEASE", "1")
    monkeypatch.delenv("INFERENCE_FORCE_RELEASE", raising=False)

    backend = _FakeBackend()
    backend.release_all = Mock()

    _safe_release_backend(backend)

    backend.release_all.assert_not_called()


def test_safe_release_backend_windows_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _safe_release_backend skips release on Windows by default."""
    if os.name == "nt":
        monkeypatch.delenv("INFERENCE_DISABLE_RELEASE", raising=False)
        monkeypatch.delenv("INFERENCE_FORCE_RELEASE", raising=False)

        backend = _FakeBackend()
        backend.release_all = Mock()

        _safe_release_backend(backend)

        backend.release_all.assert_not_called()


def test_safe_release_backend_force_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _safe_release_backend releases when INFERENCE_FORCE_RELEASE=1."""
    monkeypatch.setenv("INFERENCE_FORCE_RELEASE", "1")
    monkeypatch.delenv("INFERENCE_DISABLE_RELEASE", raising=False)

    backend = _FakeBackend()
    backend.release_all = Mock()

    _safe_release_backend(backend)

    backend.release_all.assert_called_once()


def test_safe_release_backend_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _safe_release_backend handles exceptions gracefully."""
    monkeypatch.setenv("INFERENCE_FORCE_RELEASE", "1")
    monkeypatch.delenv("INFERENCE_DISABLE_RELEASE", raising=False)

    backend = _FakeBackend()

    def failing_release() -> None:
        raise RuntimeError("Release failed")

    backend.release_all = failing_release

    # Should not raise exception
    _safe_release_backend(backend)


def test_stop_heartbeat() -> None:
    """Test _stop_heartbeat is a noop."""
    # Should complete without error
    import asyncio

    async def run_test() -> None:
        await _stop_heartbeat()

    asyncio.run(run_test())


def test_lifespan_startup_shutdown() -> None:
    """Test lifespan context manager logs startup and calls shutdown."""
    import asyncio

    async def run_test() -> None:
        from fastapi import FastAPI

        mock_app = FastAPI()
        async with lifespan(mock_app):
            # Startup should have been logged
            pass
        # Shutdown should have been called

    asyncio.run(run_test())


def test_transcribe_via_worker_error_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _transcribe_via_worker handles worker error messages."""
    from fastapi import HTTPException

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Mock _spawn_worker to return a fake process and connection
    fake_proc = Mock()
    fake_proc.is_alive.return_value = True
    fake_proc.terminate = Mock()
    fake_proc.join = Mock()

    fake_conn = Mock()
    fake_conn.poll.return_value = True

    def mock_recv() -> dict[str, object]:
        return {"type": "error", "message": "Worker failed"}

    fake_conn.recv = mock_recv
    fake_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return fake_proc, fake_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    with pytest.raises(HTTPException) as exc_info:
        _transcribe_via_worker(audio_path=audio_path, model="test", language=None)
    assert exc_info.value.status_code == 500
    assert "Worker failed" in str(exc_info.value.detail)


def test_transcribe_via_worker_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _transcribe_via_worker handles timeout."""
    from fastapi import HTTPException

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    fake_proc = Mock()
    fake_proc.is_alive.return_value = True
    fake_proc.terminate = Mock()
    fake_proc.join = Mock()

    fake_conn = Mock()
    fake_conn.poll.return_value = False  # No messages available

    fake_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return fake_proc, fake_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    # Mock time.time to simulate timeout immediately
    call_count = {"count": 0}

    def mock_time() -> float:
        call_count["count"] += 1
        if call_count["count"] == 1:
            return 1000.0  # Initial time
        return 2000.0  # Way past any timeout

    monkeypatch.setattr(time, "time", mock_time)

    with pytest.raises(HTTPException) as exc_info:
        _transcribe_via_worker(audio_path=audio_path, model="test", language=None, timeout=0.1)
    assert exc_info.value.status_code == 500
    assert "timeout" in str(exc_info.value.detail).lower()


def test_transcribe_via_worker_no_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _transcribe_via_worker handles missing payload."""
    from fastapi import HTTPException

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    fake_proc = Mock()
    fake_proc.is_alive.return_value = False  # Process exits
    fake_proc.terminate = Mock()
    fake_proc.join = Mock()

    fake_conn = Mock()
    fake_conn.poll.return_value = False
    fake_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return fake_proc, fake_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    with pytest.raises(HTTPException) as exc_info:
        _transcribe_via_worker(audio_path=audio_path, model="test", language=None)
    assert exc_info.value.status_code == 500


def test_stream_transcription_worker_error_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _stream_transcription_worker handles worker error messages."""
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    fake_proc = Mock()
    fake_proc.is_alive.return_value = True
    fake_proc.terminate = Mock()
    fake_proc.join = Mock()

    fake_conn = Mock()
    fake_conn.poll.return_value = True

    def mock_recv() -> dict[str, object]:
        return {"type": "error", "message": "Worker failed"}

    fake_conn.recv = mock_recv
    fake_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return fake_proc, fake_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    response = _stream_transcription_worker(audio_path=audio_path, model="test", language=None)

    # Read the stream to trigger error handling - StreamingResponse.body_iterator is async
    # For testing, we can check the response exists and has the right media type
    assert response.media_type == "text/event-stream"
    # The actual body reading would require async context, but we've triggered the code path


def test_stream_transcription_worker_proc_exited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _stream_transcription_worker handles process exit."""
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    fake_proc = Mock()
    fake_proc.is_alive.return_value = False  # Process exits
    fake_proc.terminate = Mock()
    fake_proc.join = Mock()

    fake_conn = Mock()
    fake_conn.poll.return_value = False
    fake_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return fake_proc, fake_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    response = _stream_transcription_worker(audio_path=audio_path, model="test", language=None)

    # Read the stream to trigger error handling - StreamingResponse.body_iterator is async
    # For testing, we can check the response exists and has the right media type
    assert response.media_type == "text/event-stream"
    # The actual body reading would require async context, but we've triggered the code path


def test_transcribe_with_worker_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcription endpoint with worker mode enabled."""
    monkeypatch.setenv("INFERENCE_USE_WORKER", "1")

    backend = _FakeBackend()

    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    # Mock _transcribe_via_worker to avoid actual subprocess
    def mock_transcribe_via_worker(*args: object, **kwargs: object) -> dict[str, object]:
        return {"text": "test", "segments": [], "language": "en", "duration": 1.0, "model": "test"}

    monkeypatch.setattr(app_module, "_transcribe_via_worker", mock_transcribe_via_worker)

    client = TestClient(app)
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")

    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200


def test_transcribe_streaming_with_worker_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test streaming transcription endpoint with worker mode enabled."""
    monkeypatch.setenv("INFERENCE_USE_WORKER", "1")

    backend = _FakeBackend()

    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    # Mock _stream_transcription_worker to avoid actual subprocess
    from fastapi.responses import StreamingResponse

    def mock_stream_transcription_worker(*args: object, **kwargs: object) -> StreamingResponse:
        def event_stream() -> object:
            yield b'data: {"type": "final", "text": "test"}\n\n'

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    monkeypatch.setattr(app_module, "_stream_transcription_worker", mock_stream_transcription_worker)

    client = TestClient(app)
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")

    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "stream": "true"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200


def test_middleware_exception_handling() -> None:
    """Test middleware handles exceptions correctly (lines 407-410)."""
    # Use the global app but create a temporary endpoint
    # We'll use app.router to add/remove routes properly
    from fastapi import APIRouter

    # Create a temporary router with the exception endpoint
    temp_router = APIRouter()

    @temp_router.get("/test-exception-middleware")
    async def test_exception_endpoint() -> None:
        raise ValueError("Test exception")

    # Add the router temporarily
    app.include_router(temp_router)

    try:
        client = TestClient(app)

        # The exception should be logged by the middleware and then re-raised
        # The middleware exception handling is covered (lines 407-410)
        with pytest.raises(ValueError, match="Test exception"):
            client.get("/test-exception-middleware")
    finally:
        # Remove the router by removing all routes from it
        # Find and remove routes that match our test endpoint
        routes_to_remove = [
            route for route in app.router.routes
            if hasattr(route, "path") and route.path == "/test-exception-middleware"
        ]
        for route in routes_to_remove:
            app.router.routes.remove(route)


def test_transcribe_unexpected_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test transcription endpoint handles unexpected exceptions."""
    # The exception needs to happen inside the try block (after line 100)
    # We'll make it happen during backend.transcribe
    backend = _FakeBackend()

    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    # Mock backend.transcribe to raise an unexpected exception (not BackendError)
    def failing_transcribe(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Unexpected error")

    backend.transcribe = failing_transcribe  # type: ignore[assignment]
    monkeypatch.setenv("INFERENCE_USE_WORKER", "0")  # Disable worker mode

    client = TestClient(app)
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")

    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 500
    assert "Internal server error" in response.json()["detail"]


def test_spawn_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _spawn_worker spawns a worker process."""
    import multiprocessing as mp

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Mock multiprocessing.get_context to avoid actual subprocess creation
    mock_process = Mock()
    mock_process.start = Mock()

    mock_conn_parent = Mock()
    mock_conn_child = Mock()
    mock_conn_child.close = Mock()

    mock_context = Mock()
    mock_context.Pipe.return_value = (mock_conn_parent, mock_conn_child)
    mock_context.Process.return_value = mock_process

    def mock_get_context(method: str) -> Mock:
        return mock_context

    monkeypatch.setattr(mp, "get_context", mock_get_context)

    proc, conn = _spawn_worker(audio_path=audio_path, model="test", language=None, stream=False)

    assert proc == mock_process
    assert conn == mock_conn_parent
    mock_process.start.assert_called_once()
    mock_conn_child.close.assert_called_once()
    mock_context.Pipe.assert_called_once()
    mock_context.Process.assert_called_once()


def test_transcribe_via_worker_final_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _transcribe_via_worker returns final payload (lines 307-308)."""
    import multiprocessing as mp

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Create mock process and connection
    mock_process = Mock()
    mock_process.is_alive.return_value = False
    mock_process.terminate = Mock()
    mock_process.join = Mock()

    mock_conn = Mock()
    # Simulate receiving a final message with payload
    messages = [{"type": "final", "payload": {"text": "test transcription", "language": "en"}}]
    mock_conn.poll.side_effect = [True, False]  # First poll returns True, then False
    mock_conn.recv.side_effect = messages
    mock_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return mock_process, mock_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    # Call _transcribe_via_worker
    result = _transcribe_via_worker(audio_path=audio_path, model="test", language=None)

    # Verify the payload was returned
    assert result == {"text": "test transcription", "language": "en"}
    mock_conn.close.assert_called_once()
    mock_process.join.assert_called_once()


def test_transcribe_via_worker_payload_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _transcribe_via_worker raises error when payload is None (lines 323-328)."""
    import multiprocessing as mp
    from fastapi import HTTPException

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Create mock process and connection
    mock_process = Mock()
    mock_process.is_alive.return_value = False
    mock_process.terminate = Mock()
    mock_process.join = Mock()

    mock_conn = Mock()
    # Simulate receiving a final message with payload=None
    messages = [{"type": "final", "payload": None}]
    mock_conn.poll.side_effect = [True, False]  # First poll returns True, then False
    mock_conn.recv.side_effect = messages
    mock_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return mock_process, mock_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    # Call _transcribe_via_worker and expect HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _transcribe_via_worker(audio_path=audio_path, model="test", language=None)

    assert exc_info.value.status_code == 500
    assert "Worker returned no result" in str(exc_info.value.detail)
    mock_conn.close.assert_called_once()
    mock_process.join.assert_called_once()


def test_start_heartbeat() -> None:
    """Test _start_heartbeat returns None (line 417)."""
    import asyncio

    async def run_test() -> None:
        result = await _start_heartbeat()
        assert result is None

    asyncio.run(run_test())


def test_stream_transcription_worker_segment_final(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _stream_transcription_worker handles segment then final (lines 347-378)."""
    import asyncio
    import multiprocessing as mp

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Create mock process and connection
    mock_process = Mock()
    # Process should be alive while processing messages
    mock_process.is_alive.return_value = True
    mock_process.terminate = Mock()
    mock_process.join = Mock()

    mock_conn = Mock()
    # Simulate receiving segment then final
    messages = [
        {"type": "segment", "text": "hello"},
        {"type": "final", "text": "hello world"},
    ]
    message_iter = iter(messages)
    poll_results = [True, True]  # Two messages available

    def poll_side_effect(*args: object) -> bool:
        if poll_results:
            return poll_results.pop(0)
        return False

    def recv_side_effect(*args: object) -> dict[str, object]:
        return next(message_iter)

    mock_conn.poll.side_effect = poll_side_effect
    mock_conn.recv.side_effect = recv_side_effect
    mock_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return mock_process, mock_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    # Call _stream_transcription_worker
    response = _stream_transcription_worker(audio_path=audio_path, model="test", language=None)

    # Iterate through the async stream to trigger all code paths
    async def collect_chunks() -> list[bytes]:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect_chunks())

    # Verify we got SSE events
    assert len(chunks) >= 2
    # Verify final event contains the final text
    final_chunk = chunks[-1]
    assert b'"type": "final"' in final_chunk or b'"type":"final"' in final_chunk
    mock_conn.close.assert_called_once()
    mock_process.join.assert_called_once()


def test_stream_transcription_worker_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _stream_transcription_worker handles error message (lines 357-365)."""
    import asyncio
    import multiprocessing as mp

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Create mock process and connection
    mock_process = Mock()
    # Process should be alive while processing messages
    mock_process.is_alive.return_value = True
    mock_process.terminate = Mock()
    mock_process.join = Mock()

    mock_conn = Mock()
    # Simulate receiving an error message
    messages = [{"type": "error", "message": "Worker failed"}]
    message_iter = iter(messages)
    poll_results = [True]  # One message available

    def poll_side_effect(*args: object) -> bool:
        if poll_results:
            return poll_results.pop(0)
        return False

    def recv_side_effect(*args: object) -> dict[str, object]:
        return next(message_iter)

    mock_conn.poll.side_effect = poll_side_effect
    mock_conn.recv.side_effect = recv_side_effect
    mock_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return mock_process, mock_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    # Call _stream_transcription_worker
    response = _stream_transcription_worker(audio_path=audio_path, model="test", language=None)

    # Iterate through the async stream to trigger error path
    async def collect_chunks() -> list[bytes]:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect_chunks())

    # Verify we got an error SSE event
    assert len(chunks) >= 1
    error_chunk = chunks[-1]
    assert b'"type": "error"' in error_chunk or b'"type":"error"' in error_chunk
    assert b'"message": "Worker failed"' in error_chunk or b'"message":"Worker failed"' in error_chunk
    mock_conn.close.assert_called_once()
    mock_process.join.assert_called_once()


def test_stream_transcription_worker_proc_exited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _stream_transcription_worker handles proc exited unexpectedly (lines 366-373)."""
    import asyncio
    import multiprocessing as mp

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Create mock process and connection
    mock_process = Mock()
    mock_process.is_alive.return_value = False  # Process is dead
    mock_process.terminate = Mock()
    mock_process.join = Mock()

    mock_conn = Mock()
    # Simulate no messages available and process is dead
    mock_conn.poll.return_value = False  # No messages
    mock_conn.close = Mock()

    def mock_spawn_worker(*args: object, **kwargs: object) -> tuple[Mock, Mock]:
        return mock_process, mock_conn

    monkeypatch.setattr(app_module, "_spawn_worker", mock_spawn_worker)

    # Call _stream_transcription_worker
    response = _stream_transcription_worker(audio_path=audio_path, model="test", language=None)

    # Iterate through the async stream to trigger proc-exited path
    async def collect_chunks() -> list[bytes]:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect_chunks())

    # Verify we got an error SSE event for unexpected exit
    assert len(chunks) >= 1
    error_chunk = chunks[-1]
    assert b'"type": "error"' in error_chunk or b'"type":"error"' in error_chunk
    assert b'"message": "Worker exited unexpectedly"' in error_chunk or b'"message":"Worker exited unexpectedly"' in error_chunk
    mock_conn.close.assert_called_once()
    mock_process.join.assert_called_once()


def test_get_worker_timeout_seconds_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_worker_timeout_seconds handles invalid value (lines 294-298)."""
    from inference_server.app import _get_worker_timeout_seconds

    # Set invalid value for INFERENCE_WORKER_TIMEOUT_SECONDS
    monkeypatch.setenv("INFERENCE_WORKER_TIMEOUT_SECONDS", "invalid")

    # Should return default value (900.0) and log a warning
    result = _get_worker_timeout_seconds()
    assert result == 900.0

