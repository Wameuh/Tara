"""Streaming extraction of previously inspected audio ZIP entries."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .zip_validation import ZipInspection, ZipPolicy, ZipValidationError


@dataclass(frozen=True, slots=True)
class ExtractedTrack:
    archive_name: str
    physical_path: Path
    size: int
    sha256: str


def extract_audio(
    archive_path: Path,
    output_directory: Path,
    inspection: ZipInspection,
    policy: ZipPolicy,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[ExtractedTrack, ...]:
    """Extract only approved members under exclusive generated names."""
    started = time.monotonic()
    output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    extracted: list[ExtractedTrack] = []
    created_paths: list[Path] = []
    actual_total = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in inspection.audio_entries:
                if cancelled and cancelled():
                    raise ZipValidationError("cancelled")
                if time.monotonic() - started > policy.timeout_seconds:
                    raise ZipValidationError("timeout")
                target = output_directory / f"{uuid.uuid4().hex}.bin"
                descriptor = os.open(
                    target,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                created_paths.append(target)
                digest = hashlib.sha256()
                written = 0
                try:
                    with archive.open(entry, "r") as source:
                        while chunk := source.read(65_536):
                            if (
                                cancelled
                                and written % 1_048_576 == 0
                                and cancelled()
                            ):
                                raise ZipValidationError("cancelled")
                            written += len(chunk)
                            actual_total += len(chunk)
                            if (
                                written > entry.file_size
                                or actual_total > policy.max_uncompressed_bytes
                            ):
                                raise ZipValidationError("input_too_large")
                            view = memoryview(chunk)
                            while view:
                                count = os.write(descriptor, view)
                                if count <= 0:
                                    raise OSError("archive write failed")
                                view = view[count:]
                            digest.update(chunk)
                    if written != entry.file_size:
                        raise ZipValidationError("input_invalid")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                extracted.append(
                    ExtractedTrack(entry.filename, target, written, digest.hexdigest())
                )
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        _cleanup(created_paths)
        raise ZipValidationError("input_invalid") from exc
    except ZipValidationError:
        _cleanup(created_paths)
        raise
    return tuple(extracted)


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
