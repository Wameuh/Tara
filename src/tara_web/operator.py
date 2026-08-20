"""Local-only audited controls; intentionally not mounted in the HTTP API."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from tara_web.config import load_config
from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.domain.models import utc_now
from tara_web.storage.backup import create_backup, restore_backup
from tara_web.storage.layout import StorageLayout


def main() -> None:
    parser = argparse.ArgumentParser(description="Tara local operator controls")
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    circuit = commands.add_parser("close-circuit")
    circuit.add_argument("provider")
    circuit.add_argument("operation_family")
    budget = commands.add_parser("set-budget")
    budget.add_argument("ceiling_micro_eur", type=int)
    commands.add_parser("backup")
    restore = commands.add_parser("restore")
    restore.add_argument("generation", type=Path)
    restore.add_argument("--not-before", type=datetime.fromisoformat)
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    if arguments.command == "restore":
        key = _backup_key(config)
        sqlite_relative = config.web.storage.sqlite_path.relative_to(
            config.web.storage.root
        )
        restore_backup(
            arguments.generation.resolve(strict=True),
            config.web.storage.root,
            key,
            sqlite_relative_path=sqlite_relative,
            minimum_created_at=arguments.not_before,
        )
        restored = ConnectionFactory(
            config.web.storage.sqlite_path, config.web.storage.root
        )
        with restored.transaction() as connection:
            connection.execute(
                "INSERT INTO operator_action_audit(action,target,created_at) "
                "VALUES('restore','authenticated-generation',?)",
                (utc_now(),),
            )
        return
    database = ConnectionFactory(
        config.web.storage.sqlite_path, config.web.storage.root
    )
    connection = database.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    if arguments.command == "backup":
        result = create_backup(
            database,
            StorageLayout(config.web.storage.root),
            config.web.storage.backups_root,
            _backup_key(config),
        )
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO operator_action_audit(action,target,created_at) "
                "VALUES('backup','authenticated-generation',?)",
                (utc_now(),),
            )
        print(result.path)
        return
    with database.transaction() as connection:
        if arguments.command == "close-circuit":
            _identity(arguments.provider, arguments.operation_family)
            connection.execute(
                "UPDATE provider_circuits SET state='closed',failure_count=0,"
                "open_until=NULL,probe_started_at=NULL,updated_at=? WHERE provider=? "
                "AND operation_family=?",
                (utc_now(), arguments.provider, arguments.operation_family),
            )
            target = f"{arguments.provider}/{arguments.operation_family}"
        else:
            ceiling = arguments.ceiling_micro_eur
            if isinstance(ceiling, bool) or not 0 <= ceiling <= 10**15:
                raise ValueError("budget ceiling is invalid")
            row = connection.execute(
                "SELECT spent_micro_eur,reserved_micro_eur FROM inference_budget "
                "WHERE id=1"
            ).fetchone()
            if row is not None and int(row[0]) + int(row[1]) > ceiling:
                raise DatabaseConflict("budget cannot be set below committed usage")
            connection.execute(
                "INSERT INTO inference_budget(id,ceiling_micro_eur,updated_at) "
                "VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET "
                "ceiling_micro_eur=excluded.ceiling_micro_eur,"
                "updated_at=excluded.updated_at",
                (ceiling, utc_now()),
            )
            target = f"ceiling:{ceiling}"
        connection.execute(
            "INSERT INTO operator_action_audit(action,target,created_at) VALUES(?,?,?)",
            (arguments.command, target, utc_now()),
        )


def _identity(provider: str, operation_family: str) -> None:
    if not all(
        isinstance(value, str) and 1 <= len(value) <= 64 and value.isascii()
        for value in (provider, operation_family)
    ):
        raise ValueError("circuit identity is invalid")


def _backup_key(config: object) -> bytes:
    value = config.backup_signing_key  # type: ignore[attr-defined]
    if value is None:
        raise ValueError("backup signing key is unavailable")
    return value.get_secret_value().encode("utf-8")


if __name__ == "__main__":
    main()
