"""Additional tests for inference_server.worker to achieve 100% coverage."""

from __future__ import annotations

import logging
from multiprocessing.connection import Connection
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from inference_server.backend import BackendError, TranscriptionBackend
from inference_server.worker import _stream_transcription, _transcribe_once, run_transcription_worker


class MockBackend(TranscriptionBackend):
    """Mock backend for testing worker functions."""

    def transcribe(self, *args: object, **kwargs: object) -> object:
        """Mock transcribe method."""
        from inference_server.models import TranscriptionResponse, TranscriptionSegment

        return TranscriptionResponse(
            text="test",
            segments=[TranscriptionSegment(start=0.0, end=1.0, text="test")],
            language="en",
            duration=1.0,
            model="test",
        )

    def stream_transcribe(self, *args: object, **kwargs: object) -> tuple[object, object]:
        """Mock stream_transcribe method."""
        from inference_server.models import TranscriptionSegment

        segments = iter([TranscriptionSegment(start=0.0, end=1.0, text="test")])

        class Info:
            duration = 1.0
            language = "en"

        return segments, Info()

    def release_all(self) -> None:
        """Mock release_all method."""
        pass


def test_run_transcription_worker_exception_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run_transcription_worker handles exceptions (lines 24-47)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()
    mock_conn.close = Mock()

    # Mock create_backend to raise an exception
    def mock_create_backend(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Backend creation failed")

    monkeypatch.setattr("inference_server.worker.create_backend", mock_create_backend)

    params = {
        "audio_path": str(Path("test.mp3")),
        "model": "test",
        "language": None,
        "stream": False,
    }

    # Should handle exception gracefully
    run_transcription_worker(mock_conn, params)

    # Should send error message
    mock_conn.send.assert_called_once()
    call_args = mock_conn.send.call_args[0][0]
    assert call_args["type"] == "error"
    assert "Backend creation failed" in call_args["message"]

    # Should close connection
    mock_conn.close.assert_called_once()


def test_run_transcription_worker_send_error_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run_transcription_worker handles exception when sending error message (line 41-42)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock(side_effect=OSError("Connection closed"))
    mock_conn.close = Mock()

    def mock_create_backend(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Backend creation failed")

    monkeypatch.setattr("inference_server.worker.create_backend", mock_create_backend)

    params = {
        "audio_path": str(Path("test.mp3")),
        "model": "test",
        "language": None,
        "stream": False,
    }

    # Should handle exception when sending error message
    run_transcription_worker(mock_conn, params)

    # Should still try to close connection
    mock_conn.close.assert_called_once()


def test_run_transcription_worker_close_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run_transcription_worker handles exception when closing connection (line 44-47)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()
    mock_conn.close = Mock(side_effect=OSError("Already closed"))

    def mock_create_backend(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Backend creation failed")

    monkeypatch.setattr("inference_server.worker.create_backend", mock_create_backend)

    params = {
        "audio_path": str(Path("test.mp3")),
        "model": "test",
        "language": None,
        "stream": False,
    }

    # Should handle exception when closing connection
    run_transcription_worker(mock_conn, params)

    # Should have tried to send error and close
    mock_conn.send.assert_called_once()
    mock_conn.close.assert_called_once()


def test_transcribe_once(tmp_path: Path) -> None:
    """Test _transcribe_once sends correct payload (lines 60-61)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()

    backend = MockBackend()
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    _transcribe_once(mock_conn, backend, audio_path, model="test", language="en")

    # Should send final message with payload
    mock_conn.send.assert_called_once()
    call_args = mock_conn.send.call_args[0][0]
    assert call_args["type"] == "final"
    assert "payload" in call_args


def test_stream_transcription(tmp_path: Path) -> None:
    """Test _stream_transcription sends segments and final message (lines 74-103)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()

    backend = MockBackend()
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    _stream_transcription(mock_conn, backend, audio_path, model="test", language="en")

    # Should send at least one segment and one final message
    assert mock_conn.send.call_count >= 2

    # Check first call is a segment
    first_call = mock_conn.send.call_args_list[0][0][0]
    assert first_call["type"] == "segment"
    assert "text" in first_call
    assert "start" in first_call
    assert "end" in first_call

    # Check last call is final
    last_call = mock_conn.send.call_args_list[-1][0][0]
    assert last_call["type"] == "final"
    assert "text" in last_call
    assert "language" in last_call


def test_stream_transcription_empty_segments(tmp_path: Path) -> None:
    """Test _stream_transcription skips empty segments."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()

    class EmptyBackend(TranscriptionBackend):
        """Backend that returns empty segments."""

        def transcribe(self, *args: object, **kwargs: object) -> object:
            raise NotImplementedError

        def stream_transcribe(self, *args: object, **kwargs: object) -> tuple[object, object]:
            from inference_server.models import TranscriptionSegment

            # Return segments with empty text (should be skipped)
            segments = iter([
                TranscriptionSegment(start=0.0, end=1.0, text=""),  # Empty, should be skipped
                TranscriptionSegment(start=1.0, end=2.0, text="hello"),  # Non-empty
            ])

            class Info:
                duration = 2.0
                language = "en"

            return segments, Info()

        def release_all(self) -> None:
            pass

    backend = EmptyBackend()
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    _stream_transcription(mock_conn, backend, audio_path, model="test", language="en")

    # Should skip empty segment, send one segment and one final
    segment_calls = [call[0][0] for call in mock_conn.send.call_args_list if call[0][0]["type"] == "segment"]
    assert len(segment_calls) == 1  # Only non-empty segment
    assert segment_calls[0]["text"] == "hello"


def test_run_transcription_worker_stream_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test run_transcription_worker handles stream mode (line 34)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()
    mock_conn.close = Mock()

    backend = MockBackend()

    def mock_create_backend(*args: object, **kwargs: object) -> object:
        return backend

    monkeypatch.setattr("inference_server.worker.create_backend", mock_create_backend)

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    params = {
        "audio_path": str(audio_path),
        "model": "test",
        "language": None,
        "stream": True,  # Stream mode
    }

    run_transcription_worker(mock_conn, params)

    # Should send messages (at least final)
    assert mock_conn.send.call_count >= 1
    mock_conn.close.assert_called_once()


def test_run_transcription_worker_non_stream_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test run_transcription_worker handles non-stream mode (line 36)."""
    mock_conn = Mock(spec=Connection)
    mock_conn.send = Mock()
    mock_conn.close = Mock()

    backend = MockBackend()

    def mock_create_backend(*args: object, **kwargs: object) -> object:
        return backend

    monkeypatch.setattr("inference_server.worker.create_backend", mock_create_backend)

    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"\x00\x00")

    params = {
        "audio_path": str(audio_path),
        "model": "test",
        "language": None,
        "stream": False,  # Non-stream mode
    }

    run_transcription_worker(mock_conn, params)

    # Should send one final message
    assert mock_conn.send.call_count == 1
    call_args = mock_conn.send.call_args[0][0]
    assert call_args["type"] == "final"
    mock_conn.close.assert_called_once()

