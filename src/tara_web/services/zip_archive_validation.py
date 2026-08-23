"""ZIP finalization runner producing the same logical rows as direct audio."""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.storage.layout import StorageLayout
from tara_web.storage.uploads import unlink_upload

from .audio_formats import canonical_audio_mime
from .input_validation import validate_audio
from .upload_sessions import new_opaque_id, sanitize_display_name, sanitize_person
from .zip_extraction import extract_audio
from .zip_validation import (
    ZIP_ERROR_CODES,
    ZipPolicy,
    ZipValidationError,
    inspect_zip,
)


@dataclass(frozen=True, slots=True)
class ZipValidationPolicy:
    archive: ZipPolicy
    ffprobe_timeout: int
    ffmpeg_timeout: int


class ZipArchiveValidationRunner:
    def __init__(
        self,
        repository: AudioUploadRepository,
        layout: StorageLayout,
        policy: ZipValidationPolicy,
    ) -> None:
        self._repository = repository
        self._layout = layout
        self._policy = policy

    def run(self, row: dict[str, object]) -> None:
        validation_id = str(row["validation_id"])
        if not self._repository.claim_validation(validation_id):
            return
        source = self._layout.upload_path(str(row["storage_path"]))
        destinations: list[Path] = []
        try:
            _integrity(
                source, int(row["declared_bytes"]), str(row["sha256_hex"])
            )
            self._repository.set_archive_phase(validation_id, "extraction")
            inspection = inspect_zip(source, self._policy.archive)
            with tempfile.TemporaryDirectory(
                prefix="zip-extract-", dir=self._layout.root
            ) as temporary:
                extracted = extract_audio(
                    source,
                    Path(temporary),
                    inspection,
                    self._policy.archive,
                    cancelled=lambda: not self._repository.validation_is_running(
                        validation_id
                    ),
                )
                self._repository.set_archive_phase(validation_id, "track_validation")
                tracks: list[dict[str, object]] = []
                for track in extracted:
                    if not self._repository.validation_is_running(validation_id):
                        raise ZipValidationError("cancelled")
                    file_id = new_opaque_id("uf")
                    managed = self._layout.upload_file(
                        str(row["session_public_id"]), file_id
                    )
                    destination = self._layout.upload_path(managed.relative_path)
                    os.replace(track.physical_path, destination)
                    destinations.append(destination)
                    extension = PurePosixPath(track.archive_name).suffix.lower()[1:]
                    display_name = sanitize_display_name(
                        PurePosixPath(track.archive_name).name
                    )
                    error_code = None
                    warning_code = None
                    duration_ms = None
                    detected_type = extension
                    try:
                        probe = validate_audio(
                            destination,
                            expected_size=track.size,
                            expected_sha256=track.sha256,
                            ffprobe_timeout=self._policy.ffprobe_timeout,
                            ffmpeg_timeout=self._policy.ffmpeg_timeout,
                        )
                        if probe.detected_type != extension:
                            raise ValueError("input_type_mismatch")
                        detected_type = probe.detected_type
                        duration_ms = probe.duration_ms
                        warning_code = probe.warning_code
                    except ValueError as exc:
                        error_code = (
                            str(exc)
                            if str(exc)
                            in {
                                "input_invalid",
                                "input_type_mismatch",
                                "input_too_large",
                                "validation_unavailable",
                            }
                            else "input_invalid"
                        )
                    tracks.append(
                        {
                            "public_id": file_id,
                            "storage_path": managed.relative_path,
                            "display_name": display_name,
                            "person": sanitize_person(
                                display_name.rsplit(".", 1)[0]
                            ),
                            "mime": canonical_audio_mime(extension),
                            "size": track.size,
                            "sha256": track.sha256,
                            "detected_type": detected_type,
                            "duration_ms": duration_ms,
                            "warning_code": warning_code,
                            "error_code": error_code,
                            "status": "invalid" if error_code else "ready",
                            "archive_name": track.archive_name,
                        }
                    )
            self._repository.set_archive_phase(validation_id, "launch_preparation")
            if not self._repository.finish_archive_validation(
                validation_id,
                tuple(tracks),
                excluded_count=inspection.excluded_entries,
            ):
                raise ZipValidationError("input_invalid")
            try:
                unlink_upload(
                    self._layout,
                    self._layout.parse_upload_path(str(row["storage_path"])),
                )
            except Exception:
                # Metadata already makes the source unreachable; orphan cleanup retries.
                pass
        except (OSError, ZipValidationError, ValueError) as exc:
            for destination in destinations:
                destination.unlink(missing_ok=True)
            code = str(exc)
            self._repository.finish_validation(
                validation_id,
                detected_type=None,
                duration_ms=None,
                warning_code=None,
                error_code=(
                    code
                    if code
                    in ZIP_ERROR_CODES
                    | {
                        "input_invalid",
                        "input_too_large",
                        "validation_unavailable",
                    }
                    else "input_invalid"
                ),
            )


def _integrity(path: Path, expected_size: int, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(65_536):
            size += len(chunk)
            digest.update(chunk)
    if size != expected_size or not hmac.compare_digest(
        digest.hexdigest(), expected_sha256
    ):
        raise ZipValidationError("zip_integrity_failed")
