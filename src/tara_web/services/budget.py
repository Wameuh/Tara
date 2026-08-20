"""Transactional global inference-budget reservations in integer micro-euros."""

from __future__ import annotations

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.domain.models import utc_now


class BudgetService:
    def __init__(
        self,
        database: ConnectionFactory,
        *,
        ceiling_micro_eur: int | None,
        reservation_micro_eur: int,
    ) -> None:
        if ceiling_micro_eur is not None and (
            isinstance(ceiling_micro_eur, bool) or ceiling_micro_eur < 0
        ):
            raise ValueError("budget ceiling is invalid")
        if (
            isinstance(reservation_micro_eur, bool)
            or reservation_micro_eur < 0
            or reservation_micro_eur > 10**15
        ):
            raise ValueError("budget reservation is invalid")
        self._database = database
        self.ceiling_micro_eur = ceiling_micro_eur
        self.reservation_micro_eur = reservation_micro_eur

    @property
    def enabled(self) -> bool:
        return self.ceiling_micro_eur is not None

    def reserve(
        self,
        job_public_id: str,
        attempt_number: int,
        *,
        connection: object | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        if connection is not None:
            return self._reserve(connection, job_public_id, attempt_number)
        with self._database.transaction() as database_connection:
            return self._reserve(database_connection, job_public_id, attempt_number)

    def _reserve(
        self, connection: object, job_public_id: str, attempt_number: int
    ) -> int:
        if not 16 <= len(job_public_id) <= 128 or attempt_number < 1:
            raise ValueError("budget reservation identity is invalid")
        now = utc_now()
        connection.execute(
            "INSERT OR IGNORE INTO inference_budget(id,ceiling_micro_eur,updated_at) "
            "VALUES(1,?,?)",
            (self.ceiling_micro_eur, now),
        )
        budget = connection.execute(
            "SELECT ceiling_micro_eur,spent_micro_eur,reserved_micro_eur "
            "FROM inference_budget WHERE id=1"
        ).fetchone()
        if budget is None or int(budget["ceiling_micro_eur"]) != self.ceiling_micro_eur:
            raise DatabaseConflict("budget configuration requires operator action")
        existing = connection.execute(
            "SELECT reserved_micro_eur,state FROM inference_budget_reservations "
            "WHERE job_public_id=? AND attempt_number=?",
            (job_public_id, attempt_number),
        ).fetchone()
        if existing is not None:
            if existing["state"] != "reserved":
                raise DatabaseConflict("budget reservation is already reconciled")
            return int(existing["reserved_micro_eur"])
        amount = self.reservation_micro_eur
        if (
            int(budget["spent_micro_eur"])
            + int(budget["reserved_micro_eur"])
            + amount
            > int(budget["ceiling_micro_eur"])
        ):
            raise DatabaseConflict("global inference budget is exhausted")
        connection.execute(
            "INSERT INTO inference_budget_reservations(job_public_id,attempt_number,"
            "reserved_micro_eur,state,created_at) VALUES(?,?,?,'reserved',?)",
            (job_public_id, attempt_number, amount, now),
        )
        connection.execute(
            "UPDATE inference_budget SET reserved_micro_eur=reserved_micro_eur+?,"
            "updated_at=? WHERE id=1",
            (amount, now),
        )
        return amount

    def reconcile(
        self,
        job_public_id: str,
        attempt_number: int,
        *,
        known_cost_micro_eur: int,
        complete: bool,
        connection: object | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        if (
            isinstance(known_cost_micro_eur, bool)
            or not 0 <= known_cost_micro_eur <= 10**15
        ):
            raise ValueError("budget reconciliation cost is invalid")
        if connection is not None:
            return self._reconcile(
                connection,
                job_public_id,
                attempt_number,
                known_cost_micro_eur,
                complete,
            )
        with self._database.transaction() as database_connection:
            return self._reconcile(
                database_connection,
                job_public_id,
                attempt_number,
                known_cost_micro_eur,
                complete,
            )

    @staticmethod
    def _reconcile(
        connection: object,
        job_public_id: str,
        attempt_number: int,
        known_cost_micro_eur: int,
        complete: bool,
    ) -> int:
        reservation = connection.execute(
            "SELECT id,reserved_micro_eur,state,charged_micro_eur FROM "
            "inference_budget_reservations WHERE job_public_id=? AND attempt_number=?",
            (job_public_id, attempt_number),
        ).fetchone()
        if reservation is None:
            return 0
        if reservation["state"] == "reconciled":
            return int(reservation["charged_micro_eur"])
        reserved = int(reservation["reserved_micro_eur"])
        charged = (
            known_cost_micro_eur
            if complete
            else max(reserved, known_cost_micro_eur)
        )
        now = utc_now()
        connection.execute(
            "UPDATE inference_budget_reservations SET state='reconciled',"
            "charged_micro_eur=?,reconciled_at=? WHERE id=? AND state='reserved'",
            (charged, now, reservation["id"]),
        )
        connection.execute(
            "UPDATE inference_budget SET reserved_micro_eur="
            "MAX(0,reserved_micro_eur-?),spent_micro_eur=spent_micro_eur+?,"
            "updated_at=? WHERE id=1",
            (reserved, charged, now),
        )
        return charged
