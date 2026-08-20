"""Private resumable upload file operations."""

from __future__ import annotations

import os
import shutil
import stat

from .layout import ManagedUploadPath, StorageError, StorageLayout


def reconcile_length(
    layout: StorageLayout, upload: ManagedUploadPath, confirmed: int
) -> None:
    descriptor, directory = _open(layout, upload, create=True)
    try:
        size = os.fstat(descriptor).st_size
        if size < confirmed:
            raise StorageError("upload_integrity_failed")
        if size > confirmed:
            os.ftruncate(descriptor, confirmed)
            os.fsync(descriptor)
            if directory >= 0:
                os.fsync(directory)
    finally:
        os.close(descriptor)
        if directory >= 0:
            os.close(directory)


def reconcile_existing(
    layout: StorageLayout, upload: ManagedUploadPath, confirmed: int
) -> None:
    """Reconcile an existing file only; never create during maintenance."""
    descriptor, directory = _open(layout, upload, create=False)
    try:
        size = os.fstat(descriptor).st_size
        if size < confirmed:
            raise StorageError("upload_integrity_failed")
        if size > confirmed:
            os.ftruncate(descriptor, confirmed)
            os.fsync(descriptor)
            if directory >= 0:
                os.fsync(directory)
    finally:
        os.close(descriptor)
        if directory >= 0:
            os.close(directory)


def write_chunk(
    layout: StorageLayout,
    upload: ManagedUploadPath,
    offset: int,
    content: bytes,
    *,
    minimum_free_bytes: int = 0,
) -> None:
    if shutil.disk_usage(layout.root).free - len(content) < minimum_free_bytes:
        raise StorageError("upload capacity unavailable")
    descriptor, directory = _open(layout, upload, create=True)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != offset:
            raise StorageError("upload offset conflict")
        view = memoryview(content)
        if not hasattr(os, "pwrite"):
            os.lseek(descriptor, offset, os.SEEK_SET)
        while view:
            written = (
                os.pwrite(descriptor, view, offset)
                if hasattr(os, "pwrite")
                else os.write(descriptor, view)
            )
            if written <= 0:
                raise StorageError("upload write failed")
            offset += written
            view = view[written:]
        os.fsync(descriptor)
        if directory >= 0:
            os.fsync(directory)
    finally:
        os.close(descriptor)
        if directory >= 0:
            os.close(directory)


def unlink_upload(layout: StorageLayout, upload: ManagedUploadPath) -> bool:
    if os.name == "nt":
        path = layout.upload_path(upload.relative_path)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("upload delete failed") from exc
    directory = layout.open_directory(upload.directory_parts)
    try:
        try:
            os.unlink(upload.filename, dir_fd=directory)
            os.fsync(directory)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("upload delete failed") from exc
    finally:
        os.close(directory)


def _open(
    layout: StorageLayout, upload: ManagedUploadPath, *, create: bool
) -> tuple[int, int]:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if create:
        flags |= os.O_CREAT
    if os.name == "nt":
        path = layout.upload_path(upload.relative_path)
        return os.open(path, flags, 0o600), -1
    directory = layout.open_directory(upload.directory_parts)
    try:
        return os.open(upload.filename, flags, 0o600, dir_fd=directory), directory
    except OSError as exc:
        os.close(directory)
        raise StorageError("upload is unavailable") from exc
