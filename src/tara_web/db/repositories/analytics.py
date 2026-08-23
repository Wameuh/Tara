"""Privacy-preserving page statistics and local operator adjustments."""

from __future__ import annotations

from dataclasses import dataclass

from tara_web.db.connection import ConnectionFactory

PUBLIC_PAGES = frozenset({"new_job", "help", "upload_session", "job"})


@dataclass(frozen=True, slots=True)
class PageViewRow:
    page: str
    today: int
    last_7_days: int
    total: int


@dataclass(frozen=True, slots=True)
class AdjustmentRow:
    amount_micro_eur: int
    note: str
    created_at: str


@dataclass(frozen=True, slots=True)
class KofiEventRow:
    event_type: str
    amount_micros: int
    currency: str
    occurred_at: str
    received_at: str
    is_test_transaction: bool


@dataclass(frozen=True, slots=True)
class FailedJobRow:
    public_id: str
    status: str
    stage: str
    error_code: str | None
    failed_at: str


class AnalyticsRepository:
    def __init__(self, database: ConnectionFactory) -> None:
        self._database = database

    def record_page_view(self, *, page: str, day: str) -> None:
        if page not in PUBLIC_PAGES:
            raise ValueError("unknown public page")
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO page_view_counts(day,page,view_count) VALUES(?,?,1) "
                "ON CONFLICT(day,page) DO UPDATE SET view_count=view_count+1",
                (day, page),
            )

    def page_views(self, *, today: str, seven_days_ago: str) -> list[PageViewRow]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT page,"
                "COALESCE(SUM(CASE WHEN day=? THEN view_count ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN day>=? AND day<=? "
                "THEN view_count ELSE 0 END),0),"
                "COALESCE(SUM(view_count),0) FROM page_view_counts "
                "GROUP BY page ORDER BY page",
                (today, seven_days_ago, today),
            ).fetchall()
        finally:
            connection.close()
        by_page = {str(row[0]): row for row in rows}
        return [
            PageViewRow(
                page=page,
                today=int(by_page.get(page, (page, 0, 0, 0))[1]),
                last_7_days=int(by_page.get(page, (page, 0, 0, 0))[2]),
                total=int(by_page.get(page, (page, 0, 0, 0))[3]),
            )
            for page in sorted(PUBLIC_PAGES)
        ]

    def add_adjustment(
        self, *, amount_micro_eur: int, note: str, created_at: str
    ) -> None:
        if amount_micro_eur == 0 or abs(amount_micro_eur) > 10**15:
            raise ValueError("invalid adjustment")
        clean_note = note.strip()
        if not 1 <= len(clean_note) <= 200:
            raise ValueError("invalid adjustment note")
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO funding_consumption_adjustments("
                "amount_micro_eur,note,created_at) VALUES(?,?,?)",
                (amount_micro_eur, clean_note, created_at),
            )

    def recent_adjustments(self, *, limit: int = 20) -> list[AdjustmentRow]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT amount_micro_eur,note,created_at "
                "FROM funding_consumption_adjustments ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [AdjustmentRow(int(row[0]), str(row[1]), str(row[2])) for row in rows]

    def recent_kofi_events(self, *, limit: int = 20) -> list[KofiEventRow]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT event_type,amount_micros,currency,occurred_at,received_at,"
                "is_test_transaction FROM kofi_payment_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [
            KofiEventRow(
                event_type=str(row[0]),
                amount_micros=int(row[1]),
                currency=str(row[2]),
                occurred_at=str(row[3]),
                received_at=str(row[4]),
                is_test_transaction=bool(row[5]),
            )
            for row in rows
        ]

    def kofi_event_counts(self) -> tuple[int, int]:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(is_test_transaction),0) "
                "FROM kofi_payment_events"
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]), int(row[1])

    def job_status_counts(self) -> dict[str, int]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT status,COUNT(*) FROM jobs GROUP BY status ORDER BY status"
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]): int(row[1]) for row in rows}

    def recent_failed_jobs(self, *, limit: int = 20) -> list[FailedJobRow]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid failed job limit")
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT j.public_id,j.status,j.stage,"
                "COALESCE(j.error_code,(SELECT a.error_code FROM job_attempts a "
                "WHERE a.job_id=j.id AND a.error_code IS NOT NULL "
                "ORDER BY a.attempt_number DESC LIMIT 1)),"
                "COALESCE(j.finished_at,j.updated_at) FROM jobs j "
                "WHERE j.status IN ('failed','timed_out','cancel_failed') "
                "ORDER BY COALESCE(j.finished_at,j.updated_at) DESC,j.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [
            FailedJobRow(
                public_id=str(row[0]),
                status=str(row[1]),
                stage=str(row[2]),
                error_code=str(row[3]) if row[3] is not None else None,
                failed_at=str(row[4]),
            )
            for row in rows
        ]
