"""Bounded local audio probing without a shell."""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MAX_PROBE_OUTPUT = 65_536


@dataclass(frozen=True)
class AudioProbeResult:
    duration_ms: int
    detected_type: str
    warning_code: str | None = None


def detect_signature(prefix: bytes) -> str:
    if prefix.startswith(b"OggS"):
        return "ogg"
    if len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xF6 == 0xF0:
        return "aac"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "m4a"
    if prefix.startswith(b"ID3") or (
        len(prefix) >= 4
        and prefix[0] == 0xFF
        and prefix[1] & 0xE0 == 0xE0
        and prefix[1] & 0x06 != 0
    ):
        return "mp3"
    raise ValueError("input_invalid")


def probe_audio(
    path: Path, *, ffprobe_timeout: int, ffmpeg_timeout: int
) -> AudioProbeResult:
    try:
        with path.open("rb") as handle:
            signature = detect_signature(handle.read(16))
    except (OSError, ValueError) as exc:
        raise ValueError("input_invalid") from exc
    environment = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"}
    source = str(path.resolve())
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name",
                "-of",
                "json",
                "-i",
                source,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=ffprobe_timeout,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError("validation_unavailable") from exc
    if completed.returncode or len(completed.stdout) > _MAX_PROBE_OUTPUT:
        raise ValueError("input_invalid")
    try:
        document = json.loads(completed.stdout)
        formats = set(str(document["format"]["format_name"]).split(","))
        duration = float(document["format"]["duration"])
        streams = document["streams"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("input_invalid") from exc
    if not math.isfinite(duration) or duration <= 0 or not isinstance(streams, list):
        raise ValueError("input_invalid")
    detected = (
        "mp3"
        if "mp3" in formats
        else "ogg"
        if "ogg" in formats
        else "aac"
        if "aac" in formats
        else "m4a"
        if formats & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
        else None
    )
    audio_streams = [
        item
        for item in streams
        if isinstance(item, dict) and item.get("codec_type") == "audio"
    ]
    if detected != signature or not audio_streams:
        raise ValueError("input_type_mismatch")
    if detected in {"aac", "m4a"} and not any(
        item.get("codec_name") == "aac" for item in audio_streams
    ):
        raise ValueError("input_type_mismatch")
    if duration > 18_000:
        raise ValueError("input_too_large")
    duration_ms = int(duration * 1000)
    try:
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", source, "-t", "1", "-f", "null", "-"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=ffmpeg_timeout,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError("validation_unavailable") from exc
    if decoded.returncode:
        raise ValueError("input_invalid")
    return AudioProbeResult(
        duration_ms,
        detected,
        "audio_duration_high" if duration >= 16_200 else None,
    )
