"""Readiness checks that must complete before the HTTP process is advertised."""

from __future__ import annotations

import sqlite3

from tara_web.db.connection import DatabaseError


def verify_database(connection: sqlite3.Connection) -> None:
    """Reject a damaged database before recovery or queue dispatch begins."""
    rows = connection.execute("PRAGMA quick_check").fetchall()
    if not rows or any(str(row[0]).lower() != "ok" for row in rows):
        raise DatabaseError("database integrity check failed")
    journal = connection.execute("PRAGMA journal_mode").fetchone()
    if journal is None or str(journal[0]).lower() != "wal":
        raise DatabaseError("database WAL mode is unavailable")
