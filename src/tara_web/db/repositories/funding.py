"""Minimal, privacy-preserving persistence for public funding totals."""

from __future__ import annotations

from dataclasses import dataclass

from tara_web.db.connection import ConnectionFactory


@dataclass(frozen=True, slots=True)
class MonthlyFundingTotals:
    donations_micros: int
    estimated_consumption_micro_eur: int
    raw_consumption_micro_eur: int
    adjustment_micro_eur: int
    estimate_partial: bool


class FundingRepository:
    def __init__(self, database: ConnectionFactory) -> None:
        self._database = database

    def record_kofi_event(
        self,
        *,
        message_id: str,
        event_type: str,
        amount_micros: int,
        currency: str,
        occurred_at: str,
        received_at: str,
        is_test_transaction: bool = False,
    ) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                "INSERT INTO kofi_payment_events("
                "message_id,event_type,amount_micros,currency,occurred_at,received_at,"
                "is_test_transaction) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(message_id) DO NOTHING",
                (
                    message_id,
                    event_type,
                    amount_micros,
                    currency,
                    occurred_at,
                    received_at,
                    int(is_test_transaction),
                ),
            )
            return result.rowcount == 1

    def monthly_totals(
        self, *, start_utc: str, end_utc: str, currency: str = "EUR"
    ) -> MonthlyFundingTotals:
        connection = self._database.connect()
        try:
            donations = connection.execute(
                "SELECT COALESCE(SUM(amount_micros),0) FROM kofi_payment_events "
                "WHERE currency=? "
                "AND event_type IN ('Tip','Donation','Subscription') "
                "AND is_test_transaction=0 "
                "AND occurred_at>=? AND occurred_at<?",
                (currency, start_utc, end_utc),
            ).fetchone()
            consumption = connection.execute(
                "SELECT COALESCE(SUM(cost_micro_eur),0),"
                "COALESCE(SUM(cost_micro_eur IS NULL),0) "
                "FROM provider_usage_attempts WHERE "
                "julianday(finished_at)>=julianday(?) "
                "AND julianday(finished_at)<julianday(?)",
                (start_utc, end_utc),
            ).fetchone()
            adjustments = connection.execute(
                "SELECT COALESCE(SUM(amount_micro_eur),0) "
                "FROM funding_consumption_adjustments "
                "WHERE julianday(created_at)>=julianday(?) "
                "AND julianday(created_at)<julianday(?)",
                (start_utc, end_utc),
            ).fetchone()
        finally:
            connection.close()
        raw_consumption = int(consumption[0])
        adjustment = int(adjustments[0])
        return MonthlyFundingTotals(
            donations_micros=int(donations[0]),
            estimated_consumption_micro_eur=max(0, raw_consumption + adjustment),
            raw_consumption_micro_eur=raw_consumption,
            adjustment_micro_eur=adjustment,
            estimate_partial=bool(consumption[1]),
        )
