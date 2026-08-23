from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tara_web.services.audio_probe import AudioProbeResult, probe_audio
from tara_web.services.input_validation import (
    UploadValidationRunner,
    ValidationPolicy,
    validate_audio,
)


def _completed(
    payload: dict[str, object], returncode: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, json.dumps(payload).encode())


@pytest.mark.parametrize(
    ("prefix", "format_name", "codec_name", "kind"),
    [
        (b"ID3x", "mp3", "mp3", "mp3"),
        (b"OggSx", "ogg", "vorbis", "ogg"),
        (b"\xff\xf1xx", "aac", "aac", "aac"),
        (b"\x00\x00\x00\x18ftypM4A ", "mov,mp4,m4a", "aac", "m4a"),
    ],
)
def test_probe_valid_media_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefix: bytes,
    format_name: str,
    codec_name: str,
    kind: str,
) -> None:
    path = tmp_path / "audio"
    path.write_bytes(prefix)
    calls: list[dict[str, object]] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(kwargs)
        return _completed(
            {
                "format": {"format_name": format_name, "duration": "1.0"},
                "streams": [{"codec_type": "audio", "codec_name": codec_name}],
            }
        )

    monkeypatch.setattr("tara_web.services.audio_probe.subprocess.run", run)
    assert probe_audio(path, ffprobe_timeout=2, ffmpeg_timeout=3).detected_type == kind
    assert calls[0]["stderr"] is subprocess.DEVNULL and calls[0]["timeout"] == 2
    assert calls[1]["stdout"] is subprocess.DEVNULL and calls[1]["timeout"] == 3


@pytest.mark.parametrize("duration", ["nan", "inf", "0", "-1"])
def test_probe_rejects_bad_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duration: str
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: _completed(
            {
                "format": {"format_name": "mp3", "duration": duration},
                "streams": [{"codec_type": "audio"}],
            }
        ),
    )
    with pytest.raises(ValueError, match="input_invalid"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


def test_probe_rejects_polyglot_and_warns_at_four_thirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: _completed(
            {
                "format": {"format_name": "wav", "duration": "16200"},
                "streams": [{"codec_type": "audio"}],
            }
        ),
    )
    with pytest.raises(ValueError, match="input_type_mismatch"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


def test_probe_rejects_non_aac_codec_in_m4a_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.m4a"
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A ")
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: _completed(
            {
                "format": {"format_name": "mov,mp4,m4a", "duration": "1"},
                "streams": [{"codec_type": "audio", "codec_name": "alac"}],
            }
        ),
    )
    with pytest.raises(ValueError, match="input_type_mismatch"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


def test_validate_audio_rejects_bad_hash(tmp_path: Path) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    with pytest.raises(ValueError, match="input_invalid"):
        validate_audio(
            path,
            expected_size=4,
            expected_sha256="0" * 64,
            ffprobe_timeout=1,
            ffmpeg_timeout=1,
        )


@pytest.mark.parametrize(
    ("duration", "code"),
    [
        ("16199.999", None),
        ("16200", "audio_duration_high"),
        ("18000", "audio_duration_high"),
        ("18000.001", "input_too_large"),
    ],
)
def test_duration_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duration: str, code: str | None
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: _completed(
            {
                "format": {"format_name": "mp3", "duration": duration},
                "streams": [{"codec_type": "audio"}],
            }
        ),
    )
    if code == "input_too_large":
        with pytest.raises(ValueError, match=code):
            probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)
    else:
        assert (
            probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1).warning_code == code
        )


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), subprocess.TimeoutExpired("ffprobe", 1)]
)
def test_ffprobe_unavailable_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(ValueError, match="validation_unavailable"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


def test_probe_rejects_no_audio_stream_and_large_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: _completed(
            {"format": {"format_name": "mp3", "duration": "1"}, "streams": []}
        ),
    )
    with pytest.raises(ValueError, match="input_type_mismatch"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, b"x" * 65_537),
    )
    with pytest.raises(ValueError, match="input_invalid"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


@pytest.mark.parametrize(
    ("probe_result", "decode_result", "code"),
    [
        (_completed({}, returncode=1), None, "input_invalid"),
        (
            _completed(
                {
                    "format": {"format_name": "mp3", "duration": "1"},
                    "streams": [{"codec_type": "audio"}],
                }
            ),
            subprocess.CompletedProcess([], 1),
            "input_invalid",
        ),
    ],
)
def test_probe_process_failures_are_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_result: subprocess.CompletedProcess[bytes],
    decode_result: subprocess.CompletedProcess[bytes] | None,
    code: str,
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    results = iter(
        [probe_result] if decode_result is None else [probe_result, decode_result]
    )
    monkeypatch.setattr(
        "tara_web.services.audio_probe.subprocess.run", lambda *a, **k: next(results)
    )
    with pytest.raises(ValueError, match=code):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


def test_ffmpeg_unavailable_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3x")
    probe = _completed(
        {
            "format": {"format_name": "mp3", "duration": "1"},
            "streams": [{"codec_type": "audio"}],
        }
    )
    calls = 0

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return probe
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr("tara_web.services.audio_probe.subprocess.run", run)
    with pytest.raises(ValueError, match="validation_unavailable"):
        probe_audio(path, ffprobe_timeout=1, ffmpeg_timeout=1)


@pytest.mark.parametrize(
    ("extension", "mime"),
    [("ogg", "audio/mpeg"), ("mp3", "audio/ogg")],
)
def test_runner_rejects_extension_and_mime_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    mime: str,
) -> None:
    finished: list[dict[str, object]] = []
    repository = SimpleNamespace(
        claim_validation=lambda _identifier: True,
        finish_validation=lambda _identifier, **result: finished.append(result),
    )
    path = tmp_path / "managed.part"
    path.write_bytes(b"ID3x")
    layout = SimpleNamespace(upload_path=lambda _relative: path)
    monkeypatch.setattr(
        "tara_web.services.input_validation.validate_audio",
        lambda *a, **k: AudioProbeResult(1000, "mp3"),
    )
    UploadValidationRunner(
        repository, layout, ValidationPolicy(ffprobe_timeout=1, ffmpeg_timeout=1)
    ).run(
        {
            "validation_id": "validation-00000001",
            "storage_path": "uploads/session/file.part",
            "declared_bytes": 4,
            "sha256_hex": hashlib.sha256(b"ID3x").hexdigest(),
            "original_filename": f"Alice.{extension}",
            "declared_mime": mime,
        }
    )
    assert finished[0]["error_code"] == "input_type_mismatch"


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe are required for real media validation",
)
@pytest.mark.parametrize(
    ("suffix", "codec"),
    [
        ("mp3", "libmp3lame"),
        ("ogg", "libvorbis"),
        ("aac", "aac"),
        ("m4a", "aac"),
    ],
)
def test_validate_real_generated_audio(tmp_path: Path, suffix: str, codec: str) -> None:
    path = tmp_path / f"sample.{suffix}"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            codec,
            "-y",
            str(path),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )
    if completed.returncode:
        pytest.skip(f"ffmpeg codec {codec} is unavailable")
    content = path.read_bytes()
    result = validate_audio(
        path,
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        ffprobe_timeout=10,
        ffmpeg_timeout=10,
    )
    assert result.detected_type == suffix
