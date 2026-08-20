from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseError


class _Connection:
    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        self.begin_calls = 0

    def execute(self, statement: str) -> None:
        if statement == "BEGIN IMMEDIATE":
            self.begin_calls += 1
            if self.failures:
                raise sqlite3.OperationalError(self.failures.pop(0))

    def commit(self) -> None: pass
    def rollback(self) -> None: pass
    def close(self) -> None: pass


def test_transaction_retries_only_bounded_database_locks(monkeypatch) -> None:
    factory = ConnectionFactory(Path("db.sqlite3"), Path("."))
    connection = _Connection(["database is locked", "database is locked"])
    monkeypatch.setattr(factory, "connect", lambda: connection)
    monkeypatch.setattr("tara_web.db.connection.time.sleep", lambda _: None)

    with factory.transaction():
        pass

    assert connection.begin_calls == 3


def test_transaction_does_not_retry_non_lock_errors(monkeypatch) -> None:
    factory = ConnectionFactory(Path("db.sqlite3"), Path("."))
    connection = _Connection(["disk I/O error"])
    monkeypatch.setattr(factory, "connect", lambda: connection)

    with pytest.raises(DatabaseError):
        with factory.transaction():
            pass

    assert connection.begin_calls == 1
