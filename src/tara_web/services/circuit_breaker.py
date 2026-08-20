"""Persistent provider/operation circuit breaker with one half-open probe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tara_web.db.connection import ConnectionFactory
from tara_web.domain.models import utc_now


class CircuitBreaker:
    def __init__(
        self, database: ConnectionFactory, *, failure_threshold: int, open_seconds: int
    ) -> None:
        if not 1 <= failure_threshold <= 1_000 or not 1 <= open_seconds <= 86_400:
            raise ValueError("circuit policy is invalid")
        self._database = database
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds

    def allow_work(self) -> bool:
        """Gate new paid work while preserving already-running attempts."""
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT provider,operation_family,state,open_until FROM "
                "provider_circuits WHERE state!='closed' ORDER BY provider,"
                "operation_family"
            ).fetchall()
            if not rows:
                return True
            now = datetime.now(UTC)
            if any(row["state"] == "half_open" for row in rows):
                return False
            for row in rows:
                try:
                    open_until = datetime.fromisoformat(
                        str(row["open_until"])
                    ).astimezone(UTC)
                    if open_until > now:
                        return False
                except ValueError:
                    return False
            row = rows[0]
            connection.execute(
                "UPDATE provider_circuits SET state='half_open',"
                "probe_started_at=?,updated_at=? WHERE provider=? AND "
                "operation_family=? AND state='open'",
                (
                    now.isoformat(),
                    now.isoformat(),
                    row["provider"],
                    row["operation_family"],
                ),
            )
            return True

    def allow(self, provider: str, operation_family: str) -> bool:
        _identity(provider, operation_family)
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT state,open_until FROM provider_circuits WHERE provider=? "
                "AND operation_family=?",
                (provider, operation_family),
            ).fetchone()
            if row is None or row["state"] == "closed":
                return True
            if row["state"] == "half_open":
                return False
            now = datetime.now(UTC)
            try:
                open_until = datetime.fromisoformat(
                    str(row["open_until"])
                ).astimezone(UTC)
            except ValueError:
                return False
            if open_until > now:
                return False
            cursor = connection.execute(
                "UPDATE provider_circuits SET state='half_open',probe_started_at=?,"
                "updated_at=? WHERE provider=? AND operation_family=? AND state='open'",
                (now.isoformat(), now.isoformat(), provider, operation_family),
            )
            return cursor.rowcount == 1

    def finish_probe(self, *, success: bool, connection: object) -> None:
        now = datetime.now(UTC)
        if success:
            connection.execute(
                "UPDATE provider_circuits SET state='closed',failure_count=0,"
                "open_until=NULL,probe_started_at=NULL,updated_at=? "
                "WHERE state='half_open'",
                (now.isoformat(),),
            )
        else:
            connection.execute(
                "UPDATE provider_circuits SET state='open',open_until=?,"
                "probe_started_at=NULL,updated_at=? WHERE state='half_open'",
                (
                    (now + timedelta(seconds=self.open_seconds)).isoformat(),
                    now.isoformat(),
                ),
            )

    def record_success(
        self,
        provider: str,
        operation_family: str,
        *,
        connection: object | None = None,
    ) -> None:
        _identity(provider, operation_family)
        if connection is not None:
            self._record_success(connection, provider, operation_family)
            return
        with self._database.transaction() as database_connection:
            self._record_success(database_connection, provider, operation_family)

    @staticmethod
    def _record_success(
        connection: object, provider: str, operation_family: str
    ) -> None:
        connection.execute(
                "INSERT INTO provider_circuits(provider,operation_family,state,"
                "failure_count,open_until,probe_started_at,updated_at) "
                "VALUES(?,?,'closed',0,NULL,NULL,?) ON CONFLICT(provider,"
                "operation_family) DO UPDATE SET state='closed',failure_count=0,"
                "open_until=NULL,probe_started_at=NULL,updated_at=excluded.updated_at",
            (provider, operation_family, utc_now()),
        )

    def record_failure(
        self,
        provider: str,
        operation_family: str,
        *,
        connection: object | None = None,
    ) -> None:
        _identity(provider, operation_family)
        if connection is not None:
            self._record_failure(connection, provider, operation_family)
            return
        with self._database.transaction() as database_connection:
            self._record_failure(database_connection, provider, operation_family)

    def _record_failure(
        self, connection: object, provider: str, operation_family: str
    ) -> None:
        now = datetime.now(UTC)
        row = connection.execute(
                "SELECT failure_count,state FROM provider_circuits WHERE provider=? "
                "AND operation_family=?",
                (provider, operation_family),
        ).fetchone()
        failures = (int(row["failure_count"]) if row else 0) + 1
        opened = failures >= self.failure_threshold or (
            row is not None and row["state"] == "half_open"
        )
        state = "open" if opened else "closed"
        until = (
            (now + timedelta(seconds=self.open_seconds)).isoformat()
            if opened
            else None
        )
        connection.execute(
                "INSERT INTO provider_circuits(provider,operation_family,state,"
                "failure_count,open_until,probe_started_at,updated_at) VALUES(?,?,?,?,"
                "?,NULL,?) ON CONFLICT(provider,operation_family) DO UPDATE SET "
                "state=excluded.state,failure_count=excluded.failure_count,"
                "open_until=excluded.open_until,probe_started_at=NULL,"
                "updated_at=excluded.updated_at",
            (provider, operation_family, state, failures, until, now.isoformat()),
        )


def _identity(provider: str, operation_family: str) -> None:
    if not all(
        isinstance(value, str) and 1 <= len(value) <= 64 and value.isascii()
        for value in (provider, operation_family)
    ):
        raise ValueError("circuit identity is invalid")
