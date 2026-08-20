"""Workers must remain unable to import the web process SQLite ownership layer."""

from __future__ import annotations

from pathlib import Path


def test_workers_do_not_import_web_database_repositories() -> None:
    source_root = Path(__file__).parents[2] / "src" / "tara"
    forbidden = ("tara_web.db", "tara_web.services.idempotency")
    offenders = [
        path
        for path in source_root.rglob("*.py")
        if any(token in path.read_text(encoding="utf-8") for token in forbidden)
    ]
    assert offenders == []
