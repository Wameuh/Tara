"""Bounded inspection of untrusted ZIP archives before any extraction."""

from __future__ import annotations

import os
import stat
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .audio_formats import AUDIO_EXTENSIONS

_WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class ZipValidationError(ValueError):
    """Stable public-safe archive rejection."""


@dataclass(frozen=True, slots=True)
class ZipPolicy:
    max_archive_bytes: int = 1_073_741_824
    max_entries: int = 1_000
    max_uncompressed_bytes: int = 5 * 1_073_741_824
    max_compression_ratio: float = 100.0
    max_depth: int = 16
    max_name_bytes: int = 1_024
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ZipInspection:
    audio_entries: tuple[zipfile.ZipInfo, ...]
    excluded_entries: int
    total_uncompressed_bytes: int


def inspect_zip(path: Path, policy: ZipPolicy) -> ZipInspection:
    """Inspect all central-directory entries without writing archive content."""
    started = time.monotonic()
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ZipValidationError("input_invalid") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ZipValidationError("input_invalid")
    if not 1 <= info.st_size <= policy.max_archive_bytes:
        raise ZipValidationError("input_too_large")

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ZipValidationError("input_invalid") from exc
    if not entries or len(entries) > policy.max_entries:
        raise ZipValidationError("input_invalid")

    audio: list[zipfile.ZipInfo] = []
    excluded = 0
    total = 0
    normalized_names: set[str] = set()
    for entry in entries:
        _deadline(started, policy)
        name = _safe_name(entry, policy)
        collision_key = unicodedata.normalize("NFC", name).casefold()
        if collision_key in normalized_names:
            raise ZipValidationError("input_invalid")
        normalized_names.add(collision_key)
        if entry.is_dir():
            continue
        if entry.flag_bits & 0x1:
            raise ZipValidationError("input_invalid")
        _regular_entry(entry)
        if entry.file_size < 0 or entry.compress_size < 0:
            raise ZipValidationError("input_invalid")
        total += entry.file_size
        if total > policy.max_uncompressed_bytes:
            raise ZipValidationError("input_too_large")
        ratio = entry.file_size / max(entry.compress_size, 1)
        if ratio > policy.max_compression_ratio:
            raise ZipValidationError("input_too_large")
        if PurePosixPath(name).suffix.lower()[1:] in AUDIO_EXTENSIONS:
            audio.append(entry)
        else:
            excluded += 1
    if not audio:
        raise ZipValidationError("input_invalid")
    return ZipInspection(tuple(audio), excluded, total)


def _safe_name(entry: zipfile.ZipInfo, policy: ZipPolicy) -> str:
    name = unicodedata.normalize("NFC", entry.filename)
    raw_parts = name.split("/")
    if (
        not name
        or name != entry.filename
        or "\x00" in name
        or "\\" in name
        or any(part in {"", ".", ".."} for part in raw_parts[:-1])
        or any(
            ":" in part
            or part.rstrip(". ") != part
            or part.split(".", 1)[0].upper() in _WINDOWS_DEVICES
            for part in raw_parts
            if part
        )
        or len(name.encode("utf-8")) > policy.max_name_bytes
    ):
        raise ZipValidationError("input_invalid")
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
        or len(posix.parts) > policy.max_depth
    ):
        raise ZipValidationError("input_invalid")
    return name


def _regular_entry(entry: zipfile.ZipInfo) -> None:
    mode = (entry.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if entry.create_system == 3 and file_type and not stat.S_ISREG(mode):
        raise ZipValidationError("input_invalid")


def _deadline(started: float, policy: ZipPolicy) -> None:
    if time.monotonic() - started > policy.timeout_seconds:
        raise ZipValidationError("timeout")
