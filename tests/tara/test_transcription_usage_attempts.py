from __future__ import annotations

from pathlib import Path

import pytest
import requests

from tara.config import TaraConfig
from tara.providers.events import UsageAttempt
from tara.transcription import (
    InferenceServerClient,
    TranscriptionResponse,
    transcribe_audio_directory,
)


def _audio(tmp_path: Path) -> tuple[Path, TaraConfig]:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "track.wav").write_bytes(b"audio")
    config = TaraConfig()
    config.transcription.streaming_enabled = False
    config.transcription.parallelism = 1
    return audio_dir, config


def test_http_transcription_emits_success_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_dir, config = _audio(tmp_path)
    attempts: list[UsageAttempt] = []
    monkeypatch.setattr(
        InferenceServerClient,
        "transcribe_file",
        lambda *args, **kwargs: TranscriptionResponse(
            text="ok", segments=[], model="parakeet:test"
        ),
    )

    transcribe_audio_directory(
        audio_dir, config, usage_attempt_callback=attempts.append
    )

    assert [(item.status, item.provider, item.model) for item in attempts] == [
        ("success", "http", "parakeet:test")
    ]


@pytest.mark.parametrize(
    ("error", "status"),
    [(RuntimeError("secret"), "failed"), (requests.Timeout("secret"), "timed_out")],
)
def test_http_transcription_emits_failure_status_without_error_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    status: str,
) -> None:
    audio_dir, config = _audio(tmp_path)
    attempts: list[UsageAttempt] = []

    def fail(*args: object, **kwargs: object) -> TranscriptionResponse:
        raise error

    monkeypatch.setattr(InferenceServerClient, "transcribe_file", fail)
    with pytest.raises(type(error)):
        transcribe_audio_directory(
            audio_dir, config, usage_attempt_callback=attempts.append
        )
    assert [item.status for item in attempts] == [status]
    assert "secret" not in str(attempts[0].parameters())


def test_http_cancellation_before_call_emits_no_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_dir, config = _audio(tmp_path)
    attempts: list[UsageAttempt] = []
    called = False

    def provider(*args: object, **kwargs: object) -> TranscriptionResponse:
        nonlocal called
        called = True
        return TranscriptionResponse(text="ok", segments=[])

    monkeypatch.setattr(InferenceServerClient, "transcribe_file", provider)

    def cancel() -> None:
        raise LookupError("cancel")

    with pytest.raises(LookupError, match="cancel"):
        transcribe_audio_directory(
            audio_dir,
            config,
            cancellation_check=cancel,
            usage_attempt_callback=attempts.append,
        )
    assert called is False
    assert attempts == []


def test_http_cancellation_after_response_records_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_dir, config = _audio(tmp_path)
    attempts: list[UsageAttempt] = []
    checks = 0
    monkeypatch.setattr(
        InferenceServerClient,
        "transcribe_file",
        lambda *args, **kwargs: TranscriptionResponse(text="ok", segments=[]),
    )

    def cancel_after() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise LookupError("cancel")

    with pytest.raises(LookupError, match="cancel"):
        transcribe_audio_directory(
            audio_dir,
            config,
            cancellation_check=cancel_after,
            usage_attempt_callback=attempts.append,
        )
    assert [item.status for item in attempts] == ["cancelled"]
