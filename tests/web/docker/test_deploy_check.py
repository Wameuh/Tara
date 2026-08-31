from __future__ import annotations

import runpy
import sqlite3
from pathlib import Path

import pytest

_module = runpy.run_path(
    str(Path(__file__).parents[3] / "scripts/docker_deploy_check.py")
)
active_job_counts = _module["active_job_counts"]


def _database(tmp_path: Path, statuses: tuple[str, ...]) -> Path:
    path = tmp_path / "tara.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
    connection.executemany(
        "INSERT INTO jobs(status) VALUES(?)",
        ((status,) for status in statuses),
    )
    connection.commit()
    connection.close()
    return path


def test_deploy_check_accepts_only_terminal_jobs(tmp_path: Path) -> None:
    path = _database(
        tmp_path,
        ("completed", "failed", "expired", "cancelled", "timed_out", "cancel_failed"),
    )

    assert active_job_counts(path) == {}


def test_deploy_check_reports_every_non_terminal_state(tmp_path: Path) -> None:
    path = _database(
        tmp_path,
        ("queued", "running", "running", "cancel_requested", "stopping"),
    )

    assert active_job_counts(path) == {
        "cancel_requested": 1,
        "queued": 1,
        "running": 2,
        "stopping": 1,
    }


def test_deploy_check_rejects_missing_or_linked_database(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unavailable"):
        active_job_counts(tmp_path / "missing.sqlite3")
    target = _database(tmp_path, ())
    link = tmp_path / "linked.sqlite3"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(SystemExit, match="invalid"):
        active_job_counts(link)
