"""Crash-recoverable artifact orchestration; callers never select a final path."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from tara.schemas.registry import load_public_result_text
from tara_web.db.connection import DatabaseError
from tara_web.db.repositories.artifacts import ArtifactRepository

from .atomic import read_regular, write_staged
from .layout import StorageError, StorageLayout

OPTIONAL_TYPES = frozenset({"cache", "prompt", "technical_error"})
FinalValidator = Callable[[bytes], None]


def validate_final_yaml_v1(content: bytes) -> None:
    """Validate current results and the explicit Task08 v1 compatibility form."""
    if not content:
        raise ValueError("final artifact is invalid")
    try:
        text = content.decode("utf-8")
        load_public_result_text(text)
    except Exception as exc:
        raise ValueError("final artifact is invalid") from exc


@dataclass(frozen=True)
class ArtifactPolicy:
    max_bytes: int = 1_073_741_824
    minimum_free_bytes: int = 268_435_456
    max_per_job: int = 64
    cleanup_batch_size: int = 100
    orphan_grace_seconds: int = 3600


@dataclass(frozen=True)
class ArtifactOutcome:
    artifact_id: int
    warning_code: str | None = None
    error_code: str | None = None


class ArtifactService:
    def __init__(
        self,
        layout: StorageLayout,
        repository: ArtifactRepository,
        policy: ArtifactPolicy | None = None,
    ) -> None:
        self._layout = layout
        self._repository = repository
        self.policy = policy or ArtifactPolicy()

    def write(
        self,
        *,
        job_id: int,
        artifact_type: str,
        retention_kind: str,
        chunks: Iterable[bytes],
        original_filename: str | None = None,
        validator: FinalValidator | None = None,
    ) -> ArtifactOutcome:
        public_id = self._job_public_id(job_id)
        destination = self._layout.new_artifact(public_id, artifact_type)
        artifact_id = self._repository.create_pending(
            job_id=job_id,
            artifact_type=artifact_type,
            retention_kind=retention_kind,
            relative_path=destination.relative_path,
            original_filename=original_filename,
            max_per_job=self.policy.max_per_job,
        )
        try:
            result = write_staged(
                self._layout,
                destination,
                chunks,
                max_bytes=self.policy.max_bytes,
                minimum_free_bytes=self.policy.minimum_free_bytes,
                hash_content=artifact_type
                in {"final_yaml", "context_input", "previous_summary_input"},
                stage=lambda value: self._repository.stage(
                    artifact_id,
                    byte_size=value.byte_size,
                    sha256_hex=value.sha256_hex,
                    temporary_name=value.temporary_name,
                ),
            )
            if artifact_type != "final_yaml":
                self._repository.mark_ready(artifact_id)
                return ArtifactOutcome(artifact_id)
        except (DatabaseError, StorageError, OSError):
            self._fail_write(artifact_id, integrity=False)
            return ArtifactOutcome(
                artifact_id,
                warning_code="service_degraded"
                if artifact_type in OPTIONAL_TYPES
                else None,
                error_code=None
                if artifact_type in OPTIONAL_TYPES
                else "artifact_write_failed",
            )
        try:
            if result.sha256_hex is None:
                raise StorageError("artifact validation failed")
            content = read_regular(
                self._layout,
                destination,
                expected_bytes=result.byte_size,
                max_bytes=self.policy.max_bytes,
            )
            validate_final_yaml_v1(content)
            if validator is not None:
                validator(content)
        except (DatabaseError, StorageError, ValueError, OSError):
            self._fail_write(artifact_id, integrity=True)
            return ArtifactOutcome(artifact_id, error_code="artifact_write_failed")
        try:
            self._repository.mark_ready(artifact_id)
            return ArtifactOutcome(artifact_id)
        except DatabaseError:
            self._fail_write(artifact_id, integrity=False)
            return ArtifactOutcome(artifact_id, error_code="artifact_write_failed")

    def read_final_yaml(self, *, artifact_id: int, job_id: int) -> bytes:
        row = self._repository.get_final_ready(artifact_id=artifact_id, job_id=job_id)
        destination = self._layout.parse_artifact_path(str(row["relative_path"]))
        try:
            content = read_regular(
                self._layout,
                destination,
                expected_bytes=int(row["byte_size"]),
                max_bytes=self.policy.max_bytes,
            )
        except StorageError:
            self._repository.mark_error(
                artifact_id, "integrity_failed", integrity_only=True
            )
            raise StorageError("result_integrity_failed") from None
        expected = str(row["sha256_hex"])
        if hashlib.sha256(content).hexdigest() != expected or len(content) != int(
            row["byte_size"]
        ):
            self._repository.mark_error(
                artifact_id, "integrity_failed", integrity_only=True
            )
            raise StorageError("result_integrity_failed")
        return content

    def read_ready_artifact(
        self, *, job_id: int, artifact_type: str, max_bytes: int
    ) -> tuple[dict[str, object], bytes] | None:
        """Read the latest ready input through the managed bounded path only."""
        if not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("artifact read limit is invalid")
        row = self._repository.get_latest_ready(
            job_id=job_id, artifact_type=artifact_type
        )
        if row is None:
            return None
        byte_size = row["byte_size"]
        if not isinstance(byte_size, int) or byte_size < 0 or byte_size > max_bytes:
            raise StorageError("artifact integrity failed")
        destination = self._layout.parse_artifact_path(str(row["relative_path"]))
        content = read_regular(
            self._layout,
            destination,
            expected_bytes=byte_size,
            max_bytes=max_bytes,
        )
        digest = row["sha256_hex"]
        if not isinstance(digest, str) or hashlib.sha256(content).hexdigest() != digest:
            raise StorageError("artifact integrity failed")
        return row, content

    def _job_public_id(self, job_id: int) -> str:
        return self._repository.get_job_public_id(job_id)

    def _fail_write(self, artifact_id: int, *, integrity: bool) -> None:
        try:
            self._repository.mark_error(
                artifact_id,
                "integrity_failed" if integrity else "write_failed",
            )
        except DatabaseError:
            pass
