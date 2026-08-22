from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.services.budget import BudgetService
from tara_web.services.circuit_breaker import CircuitBreaker


def _database(tmp_path: Path) -> ConnectionFactory:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    connection = database.connect()
    try:
        assert migrate(connection) == 17
    finally:
        connection.close()
    return database


def test_budget_reservation_is_atomic_and_reconciliation_is_idempotent(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    budget = BudgetService(
        database, ceiling_micro_eur=100, reservation_micro_eur=60
    )
    outcomes: list[tuple[str, str]] = []

    def reserve(job: str) -> None:
        try:
            budget.reserve(job, 1)
            outcomes.append((job, "reserved"))
        except DatabaseConflict:
            outcomes.append((job, "rejected"))

    workers = [
        threading.Thread(target=reserve, args=(f"job_budget_000000{i}",))
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(outcome for _, outcome in outcomes) == ["rejected", "reserved"]
    job_id = next(job for job, outcome in outcomes if outcome == "reserved")
    assert budget.reconcile(
        job_id, 1, known_cost_micro_eur=25, complete=True
    ) == 25
    assert budget.reconcile(
        job_id, 1, known_cost_micro_eur=25, complete=True
    ) == 25
    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT spent_micro_eur,reserved_micro_eur FROM inference_budget"
        ).fetchone()
    finally:
        connection.close()
    assert tuple(row) == (25, 0)


def test_unknown_final_cost_keeps_the_conservative_reservation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    budget = BudgetService(
        database, ceiling_micro_eur=100, reservation_micro_eur=60
    )
    budget.reserve("job_partial_00000001", 1)
    assert budget.reconcile(
        "job_partial_00000001", 1, known_cost_micro_eur=10, complete=False
    ) == 60


def test_circuits_are_isolated_persistent_and_allow_one_half_open_probe(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    breaker = CircuitBreaker(database, failure_threshold=2, open_seconds=60)
    breaker.record_failure("provider-a", "llm")
    assert breaker.allow("provider-a", "llm")
    breaker.record_failure("provider-a", "llm")
    assert not breaker.allow("provider-a", "llm")
    assert not breaker.allow_work()
    assert breaker.allow("provider-a", "transcription")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE provider_circuits SET open_until=? WHERE provider=? "
            "AND operation_family=?",
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "provider-a",
                "llm",
            ),
        )
    restarted = CircuitBreaker(database, failure_threshold=2, open_seconds=60)
    assert restarted.allow_work()
    assert not restarted.allow_work()
    assert not restarted.allow("provider-a", "llm")
    restarted.record_success("provider-a", "llm")
    assert restarted.allow("provider-a", "llm")


@pytest.mark.parametrize("value", [-1, True, 10**16])
def test_budget_rejects_invalid_integer_values(tmp_path: Path, value: int) -> None:
    database = _database(tmp_path)
    with pytest.raises(ValueError):
        BudgetService(
            database, ceiling_micro_eur=100, reservation_micro_eur=value
        )
