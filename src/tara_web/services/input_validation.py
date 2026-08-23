"""Streaming final integrity and format checks for one uploaded track."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path

from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.storage.layout import StorageLayout

from .audio_formats import AUDIO_MIMES
from .audio_probe import AudioProbeResult, probe_audio


def validate_audio(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    ffprobe_timeout: int,
    ffmpeg_timeout: int,
) -> AudioProbeResult:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(65_536):
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("validation_unavailable") from exc
    if size != expected_size or not hmac.compare_digest(
        digest.hexdigest(), expected_sha256
    ):
        raise ValueError("input_invalid")
    return probe_audio(
        path, ffprobe_timeout=ffprobe_timeout, ffmpeg_timeout=ffmpeg_timeout
    )


@dataclass(frozen=True)
class ValidationPolicy:
    ffprobe_timeout: int
    ffmpeg_timeout: int


class UploadValidationRunner:
    """Validate one claimed durable row and persist only stable result codes."""

    def __init__(
        self,
        repository: AudioUploadRepository,
        layout: StorageLayout,
        policy: ValidationPolicy,
    ) -> None:
        self._repository = repository
        self._layout = layout
        self._policy = policy

    def run(self, row: dict[str, object]) -> None:
        validation_id = str(row["validation_id"])
        if not self._repository.claim_validation(validation_id):
            return
        detected_type: str | None = None
        try:
            path = self._layout.upload_path(str(row["storage_path"]))
            result = validate_audio(
                path,
                expected_size=int(row["declared_bytes"]),
                expected_sha256=str(row["sha256_hex"]),
                ffprobe_timeout=self._policy.ffprobe_timeout,
                ffmpeg_timeout=self._policy.ffmpeg_timeout,
            )
            extension = str(row["original_filename"]).rsplit(".", 1)[-1].lower()
            detected_type = result.detected_type
            if detected_type != extension:
                raise ValueError("input_type_mismatch")
            mime = row["declared_mime"]
            if mime not in AUDIO_MIMES[detected_type]:
                raise ValueError("input_type_mismatch")
        except ValueError as exc:
            self._repository.finish_validation(
                validation_id,
                detected_type=detected_type,
                duration_ms=None,
                warning_code=None,
                error_code=str(exc)
                if str(exc)
                in {
                    "input_invalid",
                    "input_type_mismatch",
                    "input_too_large",
                    "validation_unavailable",
                }
                else "input_invalid",
            )
            return
        self._repository.finish_validation(
            validation_id,
            detected_type=result.detected_type,
            duration_ms=result.duration_ms,
            warning_code=result.warning_code,
            error_code=None,
        )
