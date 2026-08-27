"""Regression tests for inference admission and worker isolation boundaries."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from inference_server import app as app_module
from inference_server.app import _safe_audio_suffix, app
from inference_server.ipc import (
    InferenceIpcViolation,
    decode_worker_message,
    encode_worker_message,
)
from inference_server.worker import _scrub_worker_credentials


def test_authentication_runs_before_multipart_parsing() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            content=b"not multipart data",
            headers={"Content-Type": "application/octet-stream"},
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_capacity_admission_runs_before_multipart_parsing() -> None:
    assert app_module._REQUEST_SLOTS.acquire(blocking=False)
    assert app_module._REQUEST_SLOTS.acquire(blocking=False)
    try:
        with TestClient(
            app, headers={"Authorization": "Bearer test-inference-token"}
        ) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                content=b"not multipart data",
                headers={"Content-Type": "application/octet-stream"},
            )
        assert response.status_code == 429
    finally:
        app_module._REQUEST_SLOTS.release()
        app_module._REQUEST_SLOTS.release()


def test_stream_construction_failure_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "create_backend", lambda _model: object())
    monkeypatch.setattr(
        app_module,
        "_spawn_worker",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    with TestClient(
        app,
        headers={"Authorization": "Bearer test-inference-token"},
        raise_server_exceptions=False,
    ) as client:
        responses = [
            client.post(
                "/v1/audio/transcriptions",
                files={"file": ("sample.mp3", b"audio", "audio/mpeg")},
                data={"model": "large-v3", "stream": "true"},
            )
            for _ in range(3)
        ]
    assert [response.status_code for response in responses] == [500, 500, 500]


def test_unknown_model_is_rejected_before_backend_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def should_not_load(_model: str) -> object:
        raise AssertionError("backend must not load")

    monkeypatch.setattr(app_module, "create_backend", should_not_load)
    with TestClient(
        app, headers={"Authorization": "Bearer test-inference-token"}
    ) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("sample.mp3", b"audio", "audio/mpeg")},
            data={"model": "attacker/arbitrary-model"},
        )
    assert response.status_code == 400


def test_upload_copy_enforces_exact_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFERENCE_MAX_UPLOAD_BYTES", "1")
    with TestClient(
        app, headers={"Authorization": "Bearer test-inference-token"}
    ) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("sample.mp3", b"ab", "audio/mpeg")},
            data={"model": "large-v3"},
        )
    assert response.status_code == 413


def test_temp_suffix_is_clamped_to_known_audio_extensions() -> None:
    assert _safe_audio_suffix("recording.MP3") == ".mp3"
    assert _safe_audio_suffix("payload.so") == ".bin"
    assert _safe_audio_suffix("no-extension") == ".bin"


def test_worker_ipc_rejects_pickle_and_unknown_shapes() -> None:
    with pytest.raises(InferenceIpcViolation):
        decode_worker_message(b"\x80\x04}\x94.")
    with pytest.raises(InferenceIpcViolation):
        encode_worker_message({"type": "segment", "text": "unbounded"})


def test_worker_ipc_round_trip_uses_json_primitives() -> None:
    message = {
        "type": "segment",
        "text": "hello",
        "start": 0.0,
        "end": 1.0,
        "progress": 50.0,
    }
    assert decode_worker_message(encode_worker_message(message)) == message


def test_worker_environment_scrubs_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFERENCE_BEARER_TOKEN", "server-credential")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-credential")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

    _scrub_worker_credentials()

    assert "INFERENCE_BEARER_TOKEN" not in os.environ
    assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"
