"""Directory-anchored atomic writes for backend-managed artifact files."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .layout import ManagedArtifactPath, StorageError, StorageLayout


def parse_temporary_name(destination: ManagedArtifactPath, value: str) -> str:
    pattern = re.compile(rf"^\.{re.escape(destination.filename)}\.[a-f0-9]{{32}}\.tmp$")
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise StorageError("storage staging is invalid")
    return value


@dataclass(frozen=True)
class WriteResult:
    byte_size: int
    sha256_hex: str | None
    temporary_name: str


def write_staged(
    layout: StorageLayout,
    destination: ManagedArtifactPath,
    chunks: Iterable[bytes],
    *,
    max_bytes: int,
    minimum_free_bytes: int,
    hash_content: bool,
    stage: Callable[[WriteResult], None],
    after_directory_open: Callable[[], None] | None = None,
) -> WriteResult:
    """Fsync a private temp file, persist its metadata, then atomically rename it."""
    if not 1 <= max_bytes <= 10**15 or not 0 <= minimum_free_bytes <= 10**15:
        raise ValueError("storage limits are invalid")
    temporary = f".{destination.filename}.{uuid.uuid4().hex}.tmp"
    if os.name == "nt":
        return _write_windows(
            layout,
            destination,
            temporary,
            chunks,
            max_bytes,
            minimum_free_bytes,
            hash_content,
            stage,
        )
    directory = layout.open_directory(destination.directory_parts)
    descriptor: int | None = None
    try:
        if after_directory_open is not None:
            after_directory_open()
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        total, digest = _stream(
            descriptor, chunks, max_bytes, minimum_free_bytes, layout.root, hash_content
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        result = WriteResult(total, digest, temporary)
        stage(result)
        os.replace(
            temporary, destination.filename, src_dir_fd=directory, dst_dir_fd=directory
        )
        os.fsync(directory)
        return result
    except OSError as exc:
        raise StorageError("artifact write failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
        os.close(directory)


def promote_staged(
    layout: StorageLayout, destination: ManagedArtifactPath, temporary: str
) -> None:
    temporary = parse_temporary_name(destination, temporary)
    if os.name == "nt":
        source = layout.artifact_path(destination.relative_path).with_name(temporary)
        target = layout.artifact_path(destination.relative_path)
        source.replace(target)
        return
    directory = layout.open_directory(destination.directory_parts)
    try:
        os.replace(
            temporary, destination.filename, src_dir_fd=directory, dst_dir_fd=directory
        )
        os.fsync(directory)
    except OSError as exc:
        raise StorageError("artifact promotion failed") from exc
    finally:
        os.close(directory)


def read_regular(
    layout: StorageLayout,
    destination: ManagedArtifactPath,
    *,
    expected_bytes: int,
    max_bytes: int,
    after_directory_open: Callable[[], None] | None = None,
) -> bytes:
    if not 0 <= expected_bytes <= max_bytes:
        raise StorageError("result is unavailable")
    if os.name == "nt":
        path = layout.artifact_path(destination.relative_path)
        try:
            with path.open("rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != expected_bytes:
                    raise StorageError("result is unavailable")
                return handle.read(max_bytes + 1)
        except OSError as exc:
            raise StorageError("result is unavailable") from exc
    directory = layout.open_directory(destination.directory_parts)
    try:
        if after_directory_open is not None:
            after_directory_open()
        descriptor = os.open(
            destination.filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size != expected_bytes
            ):
                raise StorageError("result is unavailable")
            content = bytearray()
            while chunk := os.read(descriptor, 65536):
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise StorageError("result is unavailable")
            return bytes(content)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StorageError("result is unavailable") from exc
    finally:
        os.close(directory)


def unlink_regular(
    layout: StorageLayout,
    destination: ManagedArtifactPath,
    *,
    after_directory_open: Callable[[], None] | None = None,
) -> bool:
    """Return False for an absent managed file; never follow a parent symlink."""
    if os.name == "nt":
        path = layout.artifact_path(destination.relative_path)
        if path.is_symlink():
            raise StorageError("storage path is unavailable")
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("artifact deletion failed") from exc
    directory = layout.open_directory(destination.directory_parts)
    try:
        if after_directory_open is not None:
            after_directory_open()
        try:
            info = os.stat(
                destination.filename, dir_fd=directory, follow_symlinks=False
            )
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode):
            raise StorageError("storage path is unavailable")
        os.unlink(destination.filename, dir_fd=directory)
        os.fsync(directory)
        return True
    except OSError as exc:
        raise StorageError("artifact deletion failed") from exc
    finally:
        os.close(directory)


def unlink_temporary(
    layout: StorageLayout, destination: ManagedArtifactPath, temporary: str
) -> bool:
    """Remove a backend temporary only; it is never a public artifact path."""
    temporary = parse_temporary_name(destination, temporary)
    if os.name == "nt":
        path = layout.artifact_path(destination.relative_path).with_name(temporary)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("artifact deletion failed") from exc
    directory = layout.open_directory(destination.directory_parts)
    try:
        try:
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
            return True
        except FileNotFoundError:
            return False
    except OSError as exc:
        raise StorageError("artifact deletion failed") from exc
    finally:
        os.close(directory)


def _stream(
    descriptor: int,
    chunks: Iterable[bytes],
    max_bytes: int,
    minimum_free_bytes: int,
    root: object,
    hash_content: bool,
) -> tuple[int, str | None]:
    total = 0
    digest = hashlib.sha256() if hash_content else None
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise StorageError("artifact content is invalid")
        total += len(chunk)
        if total > max_bytes or shutil.disk_usage(root).free < minimum_free_bytes + len(
            chunk
        ):
            raise StorageError("storage capacity is unavailable")
        view = memoryview(chunk)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise StorageError("artifact write failed")
            view = view[written:]
        if digest:
            digest.update(chunk)
    return total, digest.hexdigest() if digest else None


def _write_windows(
    layout: StorageLayout,
    destination: ManagedArtifactPath,
    temporary: str,
    chunks: Iterable[bytes],
    max_bytes: int,
    minimum_free_bytes: int,
    hash_content: bool,
    stage: Callable[[WriteResult], None],
) -> WriteResult:
    target = layout.artifact_path(destination.relative_path)
    candidate = target.with_name(temporary)
    try:
        with candidate.open("xb") as handle:
            total, digest = _stream(
                handle.fileno(),
                chunks,
                max_bytes,
                minimum_free_bytes,
                layout.root,
                hash_content,
            )
            handle.flush()
            os.fsync(handle.fileno())
        result = WriteResult(total, digest, temporary)
        stage(result)
        layout.artifact_path(destination.relative_path)
        candidate.replace(target)
        return result
    except OSError as exc:
        raise StorageError("artifact write failed") from exc
    finally:
        candidate.unlink(missing_ok=True)
