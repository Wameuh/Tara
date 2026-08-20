"""Local-only audited controls; intentionally not mounted in the HTTP API."""

from __future__ import annotations

import argparse
from pathlib import Path

from tara_web.config import load_config
from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.domain.models import utc_now


def main() -> None:
    parser = argparse.ArgumentParser(description="Tara local operator controls")
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    circuit = commands.add_parser("close-circuit")
    circuit.add_argument("provider")
    circuit.add_argument("operation_family")
    budget = commands.add_parser("set-budget")
    budget.add_argument("ceiling_micro_eur", type=int)
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    database = ConnectionFactory(
        config.web.storage.sqlite_path, config.web.storage.root
    )
    connection = database.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
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


if __name__ == "__main__":
    main()
