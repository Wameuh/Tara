from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from inference_server import app as app_module
from inference_server.app import app, get_backend
from inference_server.backend import BackendError, TranscriptionBackend
from inference_server.models import TranscriptionResponse, TranscriptionSegment

SAMPLE_AUDIO = Path("unit_4_body.mp3")


class _FakeBackend(TranscriptionBackend):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[Path] = []
        self.stream_calls: list[Path] = []

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ) -> TranscriptionResponse:
        self.calls.append(audio_path)
        if self.fail:
            raise BackendError("boom")
        segments = [
            TranscriptionSegment(start=0.0, end=1.0, text="hello"),
            TranscriptionSegment(start=1.0, end=2.0, text="world"),
        ]
        return TranscriptionResponse(
            text="hello world",
            segments=segments,
            language=language or "auto",
            duration=2.0,
            model=model,
        )

    def stream_transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ):
        self.stream_calls.append(audio_path)

        class Info:
            def __init__(self, lang: str | None) -> None:
                self.duration = 2.0
                self.language = lang or "auto"

        segments = iter(
            [
                TranscriptionSegment(start=0.0, end=1.0, text="hello"),
                TranscriptionSegment(start=1.0, end=2.0, text="world"),
            ],
        )
        return segments, Info(language)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client with a mocked backend."""
    backend = _FakeBackend()

    # Mock create_backend to return our fake backend
    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    test_client = TestClient(app)
    yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_backend_returns_default() -> None:
    """get_backend returns a backend instance."""
    backend = get_backend()
    assert backend is not None
    # Should return FasterWhisperBackend for backward compatibility
    from inference_server.faster_whisper_backend import FasterWhisperBackend

    assert isinstance(backend, FasterWhisperBackend)


def test_transcribe_success(client: TestClient, tmp_path: Path, disable_worker_mode: None) -> None:
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")

    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "language": "fr"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "hello world"
    assert payload["language"] == "fr"
    assert payload["model"] == "large-v3"
    assert len(payload["segments"]) == 2


def test_transcribe_unsupported_format(client: TestClient, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "response_format": "text"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)
    assert response.status_code == 400
    assert "response_format" in response.json()["detail"]


def test_transcribe_backend_error(tmp_path: Path, disable_worker_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(fail=True)
    test_client = TestClient(app)

    # Mock create_backend to return our fake backend that will fail
    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr("inference_server.app.create_backend", mock_create_backend)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3"}
        response = test_client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 500
    assert "boom" in response.json()["detail"]


def test_transcribe_streaming(client: TestClient, tmp_path: Path, disable_worker_mode: None) -> None:
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")

    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "language": "fr", "stream": "true"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    events = response.text.strip().split("\n\n")
    assert any('"type": "segment"' in event for event in events)
    assert any('"type": "final"' in event for event in events)


def test_transcribe_streaming_backend_error(tmp_path: Path, disable_worker_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailBackend(_FakeBackend):
        def stream_transcribe(self, *args: Any, **kwargs: Any):
            raise BackendError("stream-fail")

    backend = _FailBackend()

    # Mock create_backend to return our failing backend
    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    test_client = TestClient(app)
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "language": "fr", "stream": "true"}
        response = test_client.post("/v1/audio/transcriptions", files=files, data=data)
    # In non-worker mode, streaming errors are caught and raised as HTTPException
    # before the StreamingResponse is created, so we get a 500 status
    assert response.status_code == 500
    # The error message should be in the response
    assert "stream-fail" in response.text


def test_transcribe_streaming_skips_empty_text(tmp_path: Path, disable_worker_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Backend(TranscriptionBackend):
        def transcribe(self, *args: Any, **kwargs: Any):
            raise NotImplementedError

        def stream_transcribe(self, *args: Any, **kwargs: Any):
            class Info:
                duration = 2.0
                language = "en"

            segments = iter(
                [
                    TranscriptionSegment(start=0.0, end=1.0, text=""),
                    TranscriptionSegment(start=1.0, end=2.0, text="hello"),
                ],
            )
            return segments, Info()

        def release_all(self) -> None:
            return

    backend = _Backend()

    # Mock create_backend to return our custom backend
    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    test_client = TestClient(app)

    with pytest.raises(NotImplementedError):
        backend.transcribe(Path("x"), model="m", language=None)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "language": "fr", "stream": "true"}
        response = test_client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    events = response.text.strip().split("\n\n")
    # first segment empty skipped, final present
    assert any('"type": "final"' in event for event in events)
    assert '"hello"' in response.text


def test_transcribe_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_worker_mode: None) -> None:
    backend = _FakeBackend()

    # Mock create_backend to return our fake backend
    def mock_create_backend(model: str) -> TranscriptionBackend:
        return backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    test_client = TestClient(app)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")

    # Force unlink to raise
    original_unlink = Path.unlink

    def failing_unlink(self: Path, *args: Any, **kwargs: Any) -> None:  # noqa: ANN001
        raise OSError("fail")

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "language": "fr"}
        response = test_client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    # restore to avoid side effects
    monkeypatch.setattr(Path, "unlink", original_unlink)

