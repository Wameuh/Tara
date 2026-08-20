"""Hardened SQLite connection creation and short write transactions."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class DatabaseError(RuntimeError):
    """Stable, public-safe persistence failure."""


class DatabaseConflict(DatabaseError):
    """A conditional mutation did not match its current revision."""


def _assert_safe_database_path(path: Path, storage_root: Path) -> None:
    """Reject links and paths escaping the dedicated persistent storage root."""
    if storage_root.is_symlink():
        raise DatabaseError("database storage must not be a symbolic link")
    raw_root = storage_root.absolute()
    raw_path = path.absolute()
    if path.exists() and path.is_symlink():
        raise DatabaseError("database file must not be a symbolic link")
    if not raw_path.is_relative_to(raw_root):
        raise DatabaseError("database path is outside configured storage")
    entry = raw_path.parent
    while True:
        if entry.exists() and entry.is_symlink():
            raise DatabaseError("database storage must not contain symbolic links")
        if entry == raw_root:
            break
        entry = entry.parent
    root = storage_root.resolve(strict=False)
    candidate = path.resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise DatabaseError("database path is outside configured storage")
    if os.name != "nt":
        for entry in (root, path.parent):
            if entry.exists() and entry.stat().st_mode & 0o077:
                raise DatabaseError("database storage permissions are unsafe")
        for entry in (
            path,
            path.with_name(f"{path.name}-wal"),
            path.with_name(f"{path.name}-shm"),
        ):
            if entry.exists() and entry.stat().st_mode & 0o077:
                raise DatabaseError("database file permissions are unsafe")


class ConnectionFactory:
    """Create independently usable SQLite connections with uniform safety settings."""

    def __init__(
        self, path: Path, storage_root: Path, *, busy_timeout_ms: int = 100
    ) -> None:
        if not isinstance(busy_timeout_ms, int) or not 1 <= busy_timeout_ms <= 10_000:
            raise ValueError("busy timeout is invalid")
        self.path = path
        self.storage_root = storage_root
        self.busy_timeout_ms = busy_timeout_ms
        self._transactions = threading.local()

    def connect(self) -> sqlite3.Connection:
        _assert_safe_database_path(self.path, self.storage_root)
        connection: sqlite3.Connection | None = None
        created = False
        try:
            if os.name != "nt" and not self.path.exists():
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.close(descriptor)
                created = True
            connection = sqlite3.connect(
                self.path, timeout=self.busy_timeout_ms / 1000, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            if os.name != "nt":
                os.chmod(self.path, 0o600)
                for suffix in ("-wal", "-shm"):
                    sidecar = self.path.with_name(f"{self.path.name}{suffix}")
                    if sidecar.exists():
                        os.chmod(sidecar, 0o600)
            return connection
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            if created:
                self.path.unlink(missing_ok=True)
            raise DatabaseError("database is unavailable") from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._transactions, "connection", None)
        if active is not None:
            yield active
            return
        connection = self.connect()
        self._transactions.connection = connection
        try:
            # SQLite can briefly reject simultaneous writers before its busy
            # handler observes a just-finished WAL transaction. Keep this
            # bounded so request latency remains controlled under admission
            # bursts, rather than leaking a transient lock as a 500 response.
            for attempt in range(5):
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 4:
                        raise
                    time.sleep(0.01 * (2**attempt))
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise DatabaseError("database operation failed") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            del self._transactions.connection
            connection.close()
