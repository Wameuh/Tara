"""Durable preparation of immutable audio job inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from tara.token_limits import TokenLimitExceeded, require_token_limit
from tara_web.db.connection import ConnectionFactory
from tara_web.domain.models import utc_now
from tara_web.storage.artifacts import ArtifactService
from tara_web.storage.atomic import read_regular, write_staged
from tara_web.storage.layout import StorageError, StorageLayout

MAX_MANIFEST_BYTES = 262_144
MAX_SESSION_TEXT_BYTES = 2_000_000
_INPUT_PATH = re.compile(r"^inputs/[a-f0-9]{32}\.bin$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class JobWorkspaceError(RuntimeError):
    """Public-safe immutable workspace preparation error."""


class JobWorkspaceService:
    def __init__(
        self,
        database: ConnectionFactory,
        layout: StorageLayout,
        *,
        artifact_service: ArtifactService | None = None,
        before_rename: Callable[[], None] | None = None,
        after_rename: Callable[[], None] | None = None,
    ) -> None:
        self._database = database
        self._layout = layout
        self._artifact_service = artifact_service
        self._before_rename = before_rename
        self._after_rename = after_rename
        self._prepare_lock = Lock()

    def prepare(self, job_id: int, public_id: str) -> str:
        with self._prepare_lock:
            try:
                return self._prepare_locked(job_id, public_id)
            except StorageError as exc:
                raise JobWorkspaceError("audio input storage is unavailable") from exc

    def prepare_request_paths(
        self, job_id: int, public_id: str
    ) -> tuple[str, str | None, str | None]:
        """Prepare immutable audio and text inputs for one worker snapshot."""
        with self._prepare_lock:
            try:
                manifest = self._prepare_locked(job_id, public_id)
                context, previous = self._session_texts(job_id)
                return (
                    manifest,
                    self._prepare_text_artifact(
                        job_id, context, "context_input", 2_000
                    ),
                    self._prepare_text_artifact(
                        job_id, previous, "previous_summary_input", 50_000
                    ),
                )
            except StorageError as exc:
                raise JobWorkspaceError("audio input storage is unavailable") from exc

    def prepare_merged_request_paths(
        self, job_id: int, public_id: str
    ) -> tuple[str, str | None, str | None]:
        """Prepare one immutable YAML input and the optional text inputs."""
        with self._prepare_lock:
            try:
                merged = self._prepare_merged_locked(job_id, public_id)
                context, previous = self._session_texts(job_id)
                return (
                    merged,
                    self._prepare_text_artifact(
                        job_id, context, "context_input", 2_000
                    ),
                    self._prepare_text_artifact(
                        job_id, previous, "previous_summary_input", 50_000
                    ),
                )
            except StorageError as exc:
                raise JobWorkspaceError(
                    "merged transcription storage is unavailable"
                ) from exc

    def _prepare_locked(self, job_id: int, public_id: str) -> str:
        rows = self._sources(job_id)
        if not rows:
            raise JobWorkspaceError("audio job has no active ready inputs")
        inputs = [self._prepare_one(job_id, public_id, row) for row in rows]
        content = json.dumps(
            {"version": 1, "job_id": public_id, "inputs": inputs},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        if len(content) > MAX_MANIFEST_BYTES:
            raise JobWorkspaceError("audio manifest is too large")
        destination = self._layout.source_manifest(public_id)
        try:
            write_staged(
                self._layout,
                destination,
                (content,),
                max_bytes=MAX_MANIFEST_BYTES,
                minimum_free_bytes=0,
                hash_content=True,
                stage=lambda _: None,
            )
            saved = read_regular(
                self._layout,
                destination,
                expected_bytes=len(content),
                max_bytes=MAX_MANIFEST_BYTES,
            )
        except StorageError as exc:
            raise JobWorkspaceError("audio manifest is unavailable") from exc
        self._validate_manifest(saved, public_id)
        return "inputs/source-manifest.json"

    def recover(self) -> list[int]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT j.id,j.public_id,j.job_type FROM jobs j JOIN "
                "job_input_preparations p ON p.job_id=j.id WHERE j.status='queued' "
                "ORDER BY j.id"
            ).fetchall()
        finally:
            connection.close()
        failures: list[int] = []
        for row in rows:
            try:
                if row["job_type"] == "merged_transcription":
                    self.prepare_merged_request_paths(
                        int(row["id"]), str(row["public_id"])
                    )
                else:
                    self.prepare(int(row["id"]), str(row["public_id"]))
            except JobWorkspaceError:
                failures.append(int(row["id"]))
        return failures

    def _sources(self, job_id: int) -> list[object]:
        connection = self._database.connect()
        try:
            return connection.execute(
                "SELECT id,public_id,storage_path,declared_bytes,sha256_hex,person,"
                "display_name,"
                "original_filename,declared_mime,detected_type,duration_ms,"
                "schema_name,schema_version,token_count FROM "
                "upload_files WHERE job_id=? AND status='ready' AND active=1 "
                "ORDER BY id",
                (job_id,),
            ).fetchall()
        finally:
            connection.close()

    def _prepare_one(
        self, job_id: int, public_id: str, row: object
    ) -> dict[str, object]:
        size, digest = int(row["declared_bytes"]), str(row["sha256_hex"])
        if size < 1 or len(digest) != 64:
            raise JobWorkspaceError("audio input metadata is invalid")
        journal = self._journal(
            job_id, public_id, row, size, digest, artifact_type="audio_input"
        )
        source = self._layout.upload_path(str(row["storage_path"]))
        target = self._layout.artifact_path(str(journal["destination_path"]))
        source_info, target_info = self._digest(source), self._digest(target)
        expected = (size, digest)
        if source_info is not None and source_info != expected:
            raise JobWorkspaceError("audio input integrity is invalid")
        if target_info is not None and target_info != expected:
            raise JobWorkspaceError("prepared audio input integrity is invalid")
        if source_info is not None and target_info is not None:
            raise JobWorkspaceError("audio input move state is ambiguous")
        if source_info is not None:
            self._move(source, target)
            if self._digest(target) != expected:
                raise JobWorkspaceError("prepared audio input integrity is invalid")
        elif target_info is None:
            raise JobWorkspaceError("prepared audio input is unavailable")
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE job_input_preparations SET state='moved',updated_at=? "
                "WHERE id=?",
                (utc_now(), journal["id"]),
            )
        filename = self._layout.parse_artifact_path(
            str(journal["destination_path"])
        ).filename
        return {
            "source_id": self._text(row["public_id"], 128),
            "path": f"inputs/{filename}",
            "person": self._text(row["person"], 255),
            "display_name": self._text(
                row["display_name"] or row["original_filename"], 255
            ),
            "mime": self._text(row["declared_mime"], 128),
            "detected_type": self._text(row["detected_type"], 16),
            "duration_ms": row["duration_ms"],
            "bytes": size,
            "sha256": digest,
        }

    def _prepare_merged_locked(self, job_id: int, public_id: str) -> str:
        rows = self._sources(job_id)
        if len(rows) != 1:
            raise JobWorkspaceError("merged transcription input is unavailable")
        row = rows[0]
        if (
            row["schema_name"] != "tara.merged_transcription"
            or row["schema_version"] is None
            or row["token_count"] is None
        ):
            raise JobWorkspaceError("merged transcription metadata is invalid")
        size, digest = int(row["declared_bytes"]), str(row["sha256_hex"])
        if size < 1 or len(digest) != 64:
            raise JobWorkspaceError("merged transcription metadata is invalid")
        journal = self._journal(
            job_id,
            public_id,
            row,
            size,
            digest,
            artifact_type="merged_transcription_input",
        )
        source = self._layout.upload_path(str(row["storage_path"]))
        target = self._layout.artifact_path(str(journal["destination_path"]))
        source_info, target_info = self._digest(source), self._digest(target)
        expected = (size, digest)
        if source_info is not None and source_info != expected:
            raise JobWorkspaceError("merged transcription integrity is invalid")
        if target_info is not None and target_info != expected:
            raise JobWorkspaceError("prepared merged transcription is invalid")
        if source_info is not None and target_info is not None:
            raise JobWorkspaceError("merged transcription move state is ambiguous")
        if source_info is not None:
            self._move(source, target)
            if self._digest(target) != expected:
                raise JobWorkspaceError("prepared merged transcription is invalid")
        elif target_info is None:
            raise JobWorkspaceError("prepared merged transcription is unavailable")
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE job_input_preparations SET state='moved',updated_at=? "
                "WHERE id=?",
                (utc_now(), journal["id"]),
            )
        filename = self._layout.parse_artifact_path(
            str(journal["destination_path"])
        ).filename
        return f"inputs/{filename}"

    def _session_texts(self, job_id: int) -> tuple[str, str]:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT s.context_text,s.previous_summaries_text FROM jobs j "
                "JOIN upload_sessions s ON s.id=j.upload_session_id WHERE j.id=?",
                (job_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise JobWorkspaceError("job session is unavailable")
        return str(row["context_text"] or ""), str(row["previous_summaries_text"] or "")

    def _prepare_text_artifact(
        self, job_id: int, text: str, artifact_type: str, token_limit: int
    ) -> str | None:
        if not text:
            return None
        if self._artifact_service is None:
            raise JobWorkspaceError("text artifact service is unavailable")
        try:
            require_token_limit(text, token_limit)
        except TokenLimitExceeded as exc:
            raise JobWorkspaceError("text input exceeds token limit") from exc
        expected = text.encode("utf-8")
        if len(expected) > MAX_SESSION_TEXT_BYTES:
            raise JobWorkspaceError("text input is unavailable")
        try:
            existing = self._artifact_service.read_ready_artifact(
                job_id=job_id,
                artifact_type=artifact_type,
                max_bytes=MAX_SESSION_TEXT_BYTES,
            )
            if existing is not None:
                row, content = existing
                if content != expected:
                    raise JobWorkspaceError("text input is inconsistent")
                return self._relative_input_path(str(row["relative_path"]))
            outcome = self._artifact_service.write(
                job_id=job_id,
                artifact_type=artifact_type,
                retention_kind="intermediate",
                chunks=(expected,),
            )
            if outcome.error_code is not None:
                raise JobWorkspaceError("text input is unavailable")
            created = self._artifact_service.read_ready_artifact(
                job_id=job_id,
                artifact_type=artifact_type,
                max_bytes=MAX_SESSION_TEXT_BYTES,
            )
            if created is None or created[1] != expected:
                raise JobWorkspaceError("text input is unavailable")
            return self._relative_input_path(str(created[0]["relative_path"]))
        except (OSError, StorageError) as exc:
            raise JobWorkspaceError("text input is unavailable") from exc

    @staticmethod
    def _relative_input_path(relative_path: str) -> str:
        parts = relative_path.split("/")
        if len(parts) != 4 or parts[0] != "jobs" or parts[2] != "inputs":
            raise JobWorkspaceError("text input is unavailable")
        return f"inputs/{parts[3]}"

    def _journal(
        self,
        job_id: int,
        public_id: str,
        row: object,
        size: int,
        digest: str,
        *,
        artifact_type: str,
    ) -> object:
        destination = self._layout.new_artifact(public_id, artifact_type)
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO job_input_preparations("
                "job_id,upload_file_id,destination_path,expected_bytes,sha256_hex,"
                "state,created_at,updated_at) VALUES(?,?,?,?,?,'prepared',?,?) "
                "ON CONFLICT(job_id,upload_file_id) DO NOTHING",
                (job_id, row["id"], destination.relative_path, size, digest, now, now),
            )
            result = connection.execute(
                "SELECT * FROM job_input_preparations "
                "WHERE job_id=? AND upload_file_id=?",
                (job_id, row["id"]),
            ).fetchone()
        if result is None or (int(result["expected_bytes"]), result["sha256_hex"]) != (
            size,
            digest,
        ):
            raise JobWorkspaceError("input journal is invalid")
        return result

    def _move(self, source: Path, target: Path) -> None:
        self._regular(source)
        if target.exists() or _is_link_or_reparse(target):
            raise JobWorkspaceError("prepared audio input already exists")
        try:
            if self._before_rename:
                self._before_rename()
            os.rename(source, target)
            self._fsync_parent(source.parent)
            self._fsync_parent(target.parent)
            self._regular(target)
            if self._after_rename:
                self._after_rename()
        except JobWorkspaceError:
            raise
        except OSError as exc:
            raise JobWorkspaceError("audio input move failed") from exc

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _digest(self, path: Path) -> tuple[int, str] | None:
        if not path.exists():
            return None
        self._regular(path)
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(65_536):
                    digest.update(chunk)
                return handle.tell(), digest.hexdigest()
        except OSError as exc:
            raise JobWorkspaceError("audio input is unavailable") from exc

    @staticmethod
    def _regular(path: Path) -> None:
        if _is_link_or_reparse(path):
            raise JobWorkspaceError("audio input is unavailable")
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise JobWorkspaceError("audio input is unavailable")
        except OSError as exc:
            raise JobWorkspaceError("audio input is unavailable") from exc

    @staticmethod
    def _text(value: object, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value.encode()) > maximum:
            raise JobWorkspaceError("audio input metadata is invalid")
        return value

    @staticmethod
    def _validate_manifest(content: bytes, public_id: str) -> None:
        parse_manifest(content, public_id)


def parse_manifest(content: bytes, public_id: str) -> dict[str, object]:
    """Parse the bounded worker contract without accepting host paths or secrets."""
    if not isinstance(public_id, str) or not public_id:
        raise JobWorkspaceError("audio manifest is invalid")
    if not isinstance(content, bytes) or len(content) > MAX_MANIFEST_BYTES:
        raise JobWorkspaceError("audio manifest is invalid")
    try:
        data = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobWorkspaceError("audio manifest is invalid") from exc
    if not isinstance(data, dict) or set(data) != {"version", "job_id", "inputs"}:
        raise JobWorkspaceError("audio manifest is invalid")
    if data["version"] != 1 or data["job_id"] != public_id:
        raise JobWorkspaceError("audio manifest is invalid")
    inputs = data["inputs"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 1000:
        raise JobWorkspaceError("audio manifest is invalid")
    required = {
        "path",
        "person",
        "display_name",
        "mime",
        "detected_type",
        "duration_ms",
        "bytes",
        "sha256",
        "source_id",
    }
    for item in inputs:
        if not isinstance(item, dict) or set(item) != required:
            raise JobWorkspaceError("audio manifest is invalid")
        if not isinstance(item["path"], str) or not _INPUT_PATH.fullmatch(item["path"]):
            raise JobWorkspaceError("audio manifest is invalid")
        for name, maximum in (
            ("person", 255),
            ("display_name", 255),
            ("mime", 128),
            ("detected_type", 16),
        ):
            value = item[name]
            if value is not None and (
                not isinstance(value, str) or len(value.encode()) > maximum
            ):
                raise JobWorkspaceError("audio manifest is invalid")
        for name, maximum in (("duration_ms", 10**12), ("bytes", 1_073_741_824)):
            value = item[name]
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= maximum
            ):
                raise JobWorkspaceError("audio manifest is invalid")
        if (
            not isinstance(item["bytes"], int)
            or isinstance(item["bytes"], bool)
            or item["bytes"] < 1
        ):
            raise JobWorkspaceError("audio manifest is invalid")
        if not isinstance(item["sha256"], str) or not _HEX.fullmatch(item["sha256"]):
            raise JobWorkspaceError("audio manifest is invalid")
        if not isinstance(item["source_id"], str) or not _SOURCE_ID.fullmatch(
            item["source_id"]
        ):
            raise JobWorkspaceError("audio manifest is invalid")
    return data


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        return bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise JobWorkspaceError("audio input is unavailable") from exc
