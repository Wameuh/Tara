"""Integration tests for inference server API with both backends."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import inference_server.app as app_module
from inference_server.app import app
from inference_server.backend import TranscriptionBackend, create_backend
from inference_server.faster_whisper_backend import FasterWhisperBackend
from inference_server.parakeet_backend import ParakeetBackend


class _FakeBackend(TranscriptionBackend):
    def __init__(self, *, fail: bool = False, backend_name: str = "fake") -> None:
        self.fail = fail
        self.backend_name = backend_name
        self.calls: list[Path] = []
        self.stream_calls: list[Path] = []

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
    ):
        from inference_server.models import TranscriptionResponse, TranscriptionSegment

        self.calls.append(audio_path)
        if self.fail:
            from inference_server.backend import BackendError

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
        from inference_server.models import TranscriptionSegment

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

    def release_all(self) -> None:
        return


@pytest.fixture()
def client() -> TestClient:
    test_client = TestClient(
        app, headers={"Authorization": "Bearer test-inference-token"}
    )
    yield test_client


def test_api_uses_faster_whisper_backend(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_worker_mode: None) -> None:
    """API selects Faster-Whisper for non-prefixed models."""
    fake_backend = _FakeBackend(backend_name="faster_whisper")

    def mock_create_backend(model: str) -> TranscriptionBackend:
        if model.startswith("parakeet:"):
            return _FakeBackend(backend_name="parakeet")
        return fake_backend

    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "large-v3", "language": "fr"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    # The endpoint creates a temporary file, so check that a call was made
    assert len(fake_backend.calls) == 1
    # The temporary file will have a random name, so just verify it's a Path
    assert isinstance(fake_backend.calls[0], Path)


def test_api_uses_parakeet_backend(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_worker_mode: None) -> None:
    """API selects Parakeet for 'parakeet:' prefixed models."""
    fake_backend = _FakeBackend(backend_name="parakeet")

    def mock_create_backend(model: str) -> TranscriptionBackend:
        if model.startswith("parakeet:"):
            return fake_backend
        return _FakeBackend(backend_name="faster_whisper")

    # Patch create_backend in the app module
    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "parakeet:nvidia/parakeet-tdt-0.6b-v3", "language": "fr"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    # The endpoint creates a temporary file, so check that a call was made
    assert len(fake_backend.calls) == 1
    # The temporary file will have a random name, so just verify it's a Path
    assert isinstance(fake_backend.calls[0], Path)
    payload = response.json()
    assert payload["model"] == "parakeet:nvidia/parakeet-tdt-0.6b-v3"


def test_api_parakeet_transcription(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_worker_mode: None) -> None:
    """Full end-to-end Parakeet transcription via API."""
    fake_backend = _FakeBackend(backend_name="parakeet")

    def mock_create_backend(model: str) -> TranscriptionBackend:
        return fake_backend

    # Patch create_backend in the app module
    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "parakeet:nvidia/parakeet-tdt-0.6b-v3"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "hello world"
    assert payload["model"] == "parakeet:nvidia/parakeet-tdt-0.6b-v3"
    assert len(payload["segments"]) == 2


def test_api_parakeet_streaming(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_worker_mode: None) -> None:
    """Parakeet streaming transcription via API."""
    fake_backend = _FakeBackend(backend_name="parakeet")

    def mock_create_backend(model: str) -> TranscriptionBackend:
        return fake_backend

    # Patch create_backend in the app module
    monkeypatch.setattr(app_module, "create_backend", mock_create_backend)

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"\x00\x00")
    with audio_path.open("rb") as handle:
        files = {"file": (audio_path.name, handle, "audio/mpeg")}
        data = {"model": "parakeet:nvidia/parakeet-tdt-0.6b-v3", "stream": "true"}
        response = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert response.status_code == 200
    events = response.text.strip().split("\n\n")
    assert any('"type": "segment"' in event for event in events)
    assert any('"type": "final"' in event for event in events)


def test_backend_factory_integration() -> None:
    """Test that create_backend works correctly for both backend types."""
    # Test Faster-Whisper backend
    backend1 = create_backend("large-v3")
    assert isinstance(backend1, FasterWhisperBackend)

    # Test Parakeet backend
    backend2 = create_backend("parakeet:nvidia/parakeet-tdt-0.6b-v3")
    assert isinstance(backend2, ParakeetBackend)

    # Test singleton behavior
    backend3 = create_backend("large-v3")
    assert backend1 is backend3

    backend4 = create_backend("parakeet:nvidia/parakeet-tdt-0.6b-v3")
    assert backend2 is backend4





