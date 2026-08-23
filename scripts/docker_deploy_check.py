#!/usr/bin/env python3
"""Block a live deployment while any Tara job is non-terminal."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

_TERMINAL_STATUSES = (
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "cancel_failed",
)


def active_job_counts(database_path: Path) -> dict[str, int]:
    try:
        info = os.lstat(database_path)
    except OSError as exc:
        raise SystemExit("deployment check database is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SystemExit("deployment check database is invalid")
    placeholders = ",".join("?" for _ in _TERMINAL_STATUSES)
    try:
        connection = sqlite3.connect(
            f"file:{database_path}?mode=ro",
            uri=True,
            timeout=5,
        )
        rows = connection.execute(
            "SELECT status,COUNT(*) FROM jobs "
            f"WHERE status NOT IN ({placeholders}) GROUP BY status ORDER BY status",
            _TERMINAL_STATUSES,
        ).fetchall()
    except sqlite3.Error as exc:
        raise SystemExit("deployment check database query failed") from exc
    finally:
        if "connection" in locals():
            connection.close()
    return {str(status): int(count) for status, count in rows}


def main() -> None:
    database_path = Path(
        os.environ.get("TARA_WEB_SQLITE_PATH", "/data/runtime/db/tara-web.sqlite3")
    )
    counts = active_job_counts(database_path)
    if counts:
        summary = ", ".join(f"{status}={count}" for status, count in counts.items())
        raise SystemExit(f"deployment blocked: non-terminal jobs ({summary})")
    print("deployment check passed: no non-terminal jobs")


if __name__ == "__main__":
    main()
