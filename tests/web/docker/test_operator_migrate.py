from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from tara_web.db.migrations import MIGRATIONS
from tara_web.operator import main


def test_operator_migrate_is_explicit_and_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    backups = tmp_path / "backups"
    runtime.mkdir(mode=0o700)
    backups.mkdir(mode=0o700)
    config = tmp_path / "web.yaml"
    config.write_text(
        """webinterface:
  storage:
    root: ./runtime
    backups_root: ./backups
    sqlite_path: ./runtime/tara.sqlite3
  public_url: http://127.0.0.1:8000
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["tara-web-operator", "--config", str(config), "migrate"]
    )
    main()
    connection = sqlite3.connect(runtime / "tara.sqlite3")
    try:
        assert connection.execute(
            "SELECT version FROM schema_version WHERE id=1"
        ).fetchone()[0] == MIGRATIONS[-1].version
        assert connection.execute(
            "SELECT action,target FROM operator_action_audit ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("migrate", "current-schema")
    finally:
        connection.close()
