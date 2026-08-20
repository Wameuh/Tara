"""Isolated validation for untrusted merged-transcription YAML uploads."""

from __future__ import annotations

import hashlib
import hmac
import multiprocessing
import os
import stat
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tara.schemas.merged_transcription import SCHEMA_NAME
from tara.schemas.registry import load_merged_transcription
from tara.token_limits import count_tokens
from tara.web_contracts import ErrorCode
from tara.yaml_utils import YamlLimits, read_yaml
from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.storage.layout import StorageLayout


@dataclass(frozen=True)
class MergedValidationPolicy:
    max_bytes: int
    max_tokens: int
    timeout_seconds: int
    memory_bytes: int
    max_depth: int = 64
    max_nodes: int = 1_000_000
    max_scalar_chars: int = 32 * 1024 * 1024
    max_aliases: int = 0


def _regular_file(path: Path) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("input_invalid")
    if os.name == "nt" and bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("input_invalid")


def _integrity(path: Path, expected_size: int, expected_sha256: str) -> None:
    _regular_file(path)
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input_invalid")
        while chunk := os.read(descriptor, 65_536):
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or size != expected_size
        or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
    ):
        raise ValueError("input_invalid")


def _error_path(error: ValidationError) -> str | None:
    errors = error.errors(include_url=False, include_context=False, include_input=False)
    if not errors:
        return None
    path = ".".join(str(part) for part in errors[0].get("loc", ()))
    return path[:256] or None


def _validation_worker(
    connection: Connection,
    path_text: str,
    max_tokens: int,
    memory_bytes: int,
    timeout_seconds: int,
    yaml_limits: YamlLimits,
) -> None:
    try:
        if os.name != "nt":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(
                resource.RLIMIT_CPU, (timeout_seconds, timeout_seconds + 1)
            )
        path = Path(path_text)
        raw = read_yaml(path, limits=yaml_limits)
        if not isinstance(raw, dict):
            raise ValueError("input_invalid")
        source_version = (
            str(raw.get("schema_version"))
            if raw.get("schema_name") == SCHEMA_NAME
            else "legacy-v0"
        )
        merged = load_merged_transcription(path, limits=yaml_limits)
        tokens = count_tokens(merged.content.text)
        if tokens > max_tokens:
            connection.send({"ok": False, "error_code": "input_too_large"})
            return
        connection.send(
            {
                "ok": True,
                "schema_name": SCHEMA_NAME,
                "schema_version": source_version,
                "token_count": tokens,
            }
        )
    except ValidationError as exc:
        connection.send(
            {"ok": False, "error_code": "input_invalid", "error_path": _error_path(exc)}
        )
    except (MemoryError, OSError):
        connection.send({"ok": False, "error_code": "input_invalid"})
    except Exception:
        connection.send({"ok": False, "error_code": "input_invalid"})
    finally:
        connection.close()


class MergedTranscriptionValidationRunner:
    """Validate YAML in a killable child and persist only bounded metadata."""

    def __init__(
        self,
        repository: AudioUploadRepository,
        layout: StorageLayout,
        policy: MergedValidationPolicy,
    ) -> None:
        self._repository = repository
        self._layout = layout
        self._policy = policy

    def run(self, row: dict[str, object]) -> None:
        validation_id = str(row["validation_id"])
        if not self._repository.claim_validation(validation_id):
            return
        result: dict[str, Any]
        try:
            size = int(row["declared_bytes"])
            if size > self._policy.max_bytes:
                raise ValueError("input_too_large")
            path = self._layout.upload_path(str(row["storage_path"]))
            _integrity(path, size, str(row["sha256_hex"]))
            result = self._run_child(path)
        except ValueError as exc:
            code = str(exc)
            result = {
                "ok": False,
                "error_code": code
                if code in {"input_invalid", "input_too_large"}
                else "input_invalid",
            }
        except OSError:
            result = {"ok": False, "error_code": "input_invalid"}
        self._repository.finish_validation(
            validation_id,
            detected_type=None,
            duration_ms=None,
            warning_code=None,
            error_code=(None if result.get("ok") else str(result["error_code"])),
            schema_name=(str(result["schema_name"]) if result.get("ok") else None),
            schema_version=(
                str(result["schema_version"]) if result.get("ok") else None
            ),
            token_count=(int(result["token_count"]) if result.get("ok") else None),
            error_path=(
                str(result["error_path"]) if result.get("error_path") else None
            ),
        )

    def _run_child(self, path: Path) -> dict[str, Any]:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_validation_worker,
            args=(
                child,
                str(path),
                self._policy.max_tokens,
                self._policy.memory_bytes,
                self._policy.timeout_seconds,
                YamlLimits(
                    max_bytes=self._policy.max_bytes,
                    max_depth=self._policy.max_depth,
                    max_nodes=self._policy.max_nodes,
                    max_scalar_chars=self._policy.max_scalar_chars,
                    max_aliases=self._policy.max_aliases,
                    max_seconds=float(self._policy.timeout_seconds),
                ),
            ),
            daemon=False,
        )
        process.start()
        child.close()
        try:
            if not parent.poll(self._policy.timeout_seconds):
                process.kill()
                process.join(timeout=5)
                return {"ok": False, "error_code": ErrorCode.TIMEOUT.value}
            result = parent.recv()
            if not isinstance(result, dict) or result.get("ok") not in {True, False}:
                return {"ok": False, "error_code": "input_invalid"}
            return result
        except (EOFError, OSError):
            return {"ok": False, "error_code": "input_invalid"}
        finally:
            parent.close()
            if process.is_alive():
                process.kill()
            process.join(timeout=5)
