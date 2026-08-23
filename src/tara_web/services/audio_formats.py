"""Canonical web-upload audio formats and their public MIME types."""

from __future__ import annotations

AUDIO_EXTENSIONS = frozenset({"mp3", "ogg", "aac", "m4a"})
AUDIO_FORMAT_LABEL = "MP3, OGG, AAC, M4A"
AUDIO_MIMES: dict[str, frozenset[str | None]] = {
    "mp3": frozenset({None, "audio/mpeg", "audio/mp3"}),
    "ogg": frozenset({None, "audio/ogg", "application/ogg"}),
    "aac": frozenset({None, "audio/aac", "audio/aacp", "audio/x-aac"}),
    "m4a": frozenset({None, "audio/mp4", "audio/m4a", "audio/x-m4a"}),
}


def canonical_audio_mime(kind: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "aac": "audio/aac",
        "m4a": "audio/mp4",
    }[kind]
