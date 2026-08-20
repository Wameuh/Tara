"""Strict backend-owned artifact layout and POSIX directory anchors."""

from __future__ import annotations

import os
import re
import stat
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tara_web.domain.models import validate_relative_storage_path

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SAFE_PHYSICAL_NAME = re.compile(r"^[a-f0-9]{32}\.(?:bin|yaml)$")
SAFE_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9_-]{16,128}\.part$")
SOURCE_MANIFEST_NAME = "source-manifest.json"
_INPUT_TYPES = frozenset(
    {
        "audio_input",
        "merged_transcription_input",
        "context_input",
        "previous_summary_input",
    }
)
_WORK_TYPES = frozenset(
    {
        "raw_transcription",
        "scene_data",
        "blackboard",
        "prompt",
        "cache",
        "technical_error",
    }
)
_FINAL_TYPES = frozenset({"final_yaml"})
ARTIFACT_TYPES = _INPUT_TYPES | _WORK_TYPES | _FINAL_TYPES


class StorageError(RuntimeError):
    """Public-safe storage failure; never includes a filesystem path."""


@dataclass(frozen=True)
class ManagedArtifactPath:
    relative_path: str
    directory_parts: tuple[str, ...]
    filename: str


@dataclass(frozen=True)
class ManagedUploadPath:
    relative_path: str
    directory_parts: tuple[str, ...]
    filename: str


class StorageLayout:
    def __init__(self, root: Path) -> None:
        self.root = root

    def area_for(self, artifact_type: str) -> str:
        if artifact_type in _INPUT_TYPES:
            return "inputs"
        if artifact_type in _WORK_TYPES:
            return "work"
        if artifact_type in _FINAL_TYPES:
            return "result"
        raise StorageError("artifact type is invalid")

    def create_job_layout(self, job_public_id: str) -> None:
        for area in ("inputs", "work", "result"):
            self._mkdir(self.root / "jobs" / self._id(job_public_id) / area)

    def upload_file(self, session_id: str, file_id: str) -> ManagedUploadPath:
        session = self._id(session_id)
        file_name = f"{self._id(file_id)}.part"
        directory = self.root / "uploads" / session
        self._mkdir(directory)
        return ManagedUploadPath(
            f"uploads/{session}/{file_name}", ("uploads", session), file_name
        )

    def parse_upload_path(self, relative_path: str) -> ManagedUploadPath:
        try:
            validate_relative_storage_path(relative_path)
        except ValueError as exc:
            raise StorageError("upload path is invalid") from exc
        parts = PurePosixPath(relative_path).parts
        if (
            len(parts) != 3
            or parts[0] != "uploads"
            or not SAFE_ID.fullmatch(parts[1])
            or not SAFE_UPLOAD_NAME.fullmatch(parts[2])
        ):
            raise StorageError("upload path is invalid")
        return ManagedUploadPath(relative_path, tuple(parts[:2]), parts[2])

    def upload_path(self, relative_path: str) -> Path:
        managed = self.parse_upload_path(relative_path)
        candidate = self.root.joinpath(*managed.directory_parts, managed.filename)
        self._assert_components(candidate)
        return candidate

    def new_artifact(
        self, job_public_id: str, artifact_type: str
    ) -> ManagedArtifactPath:
        area = self.area_for(artifact_type)
        job = self._id(job_public_id)
        self.create_job_layout(job)
        extension = (
            "yaml"
            if area == "result" or artifact_type == "merged_transcription_input"
            else "bin"
        )
        filename = f"{uuid.uuid4().hex}.{extension}"
        return ManagedArtifactPath(
            f"jobs/{job}/{area}/{filename}", ("jobs", job, area), filename
        )

    def parse_artifact_path(self, relative_path: str) -> ManagedArtifactPath:
        try:
            validate_relative_storage_path(relative_path)
        except ValueError as exc:
            raise StorageError("storage path is invalid") from exc
        if unicodedata.normalize("NFC", relative_path) != relative_path:
            raise StorageError("storage path is invalid")
        parts = PurePosixPath(relative_path).parts
        if (
            len(parts) != 4
            or parts[0] != "jobs"
            or not SAFE_ID.fullmatch(parts[1])
            or parts[2] not in {"inputs", "work", "result"}
            or (
                not SAFE_PHYSICAL_NAME.fullmatch(parts[3])
                and not (parts[2] == "inputs" and parts[3] == SOURCE_MANIFEST_NAME)
            )
            or (parts[2] == "result" and not parts[3].endswith(".yaml"))
            or (
                parts[2] == "work" and not parts[3].endswith(".bin")
            )
            or (
                parts[2] == "inputs"
                and not parts[3].endswith((".bin", ".yaml"))
                and parts[3] != SOURCE_MANIFEST_NAME
            )
        ):
            raise StorageError("storage path is invalid")
        return ManagedArtifactPath(relative_path, tuple(parts[:3]), parts[3])

    def artifact_path(self, relative_path: str) -> Path:
        managed = self.parse_artifact_path(relative_path)
        candidate = self.root.joinpath(*managed.directory_parts, managed.filename)
        self._assert_components(candidate)
        return candidate

    def source_manifest(self, job_public_id: str) -> ManagedArtifactPath:
        job = self._id(job_public_id)
        self.create_job_layout(job)
        return ManagedArtifactPath(
            f"jobs/{job}/inputs/{SOURCE_MANIFEST_NAME}",
            ("jobs", job, "inputs"),
            SOURCE_MANIFEST_NAME,
        )

    def open_directory(self, parts: tuple[str, ...]) -> int:
        """Open a no-follow directory chain rooted at storage.root on POSIX."""
        self._assert_components(self.root.joinpath(*parts))
        if os.name == "nt":
            return -1
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.root, flags)
        try:
            for part in parts:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except OSError as exc:
            os.close(descriptor)
            raise StorageError("storage directory is unavailable") from exc

    def _id(self, value: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise StorageError("storage identifier is invalid")
        return value

    def _mkdir(self, path: Path) -> None:
        old_umask = os.umask(0o077) if os.name != "nt" else None
        try:
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
        finally:
            if old_umask is not None:
                os.umask(old_umask)
        self._assert_components(path)

    def _assert_components(self, path: Path) -> None:
        root = self.root.absolute()
        current = path.absolute()
        if not current.is_relative_to(root):
            raise StorageError("storage path is invalid")
        while True:
            if current.exists() and _is_link_or_reparse(current):
                raise StorageError("storage path is unavailable")
            if current == root:
                break
            current = current.parent


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        return bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError as exc:
        raise StorageError("storage path is unavailable") from exc
