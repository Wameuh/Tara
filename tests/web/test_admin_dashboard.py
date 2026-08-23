from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from tara_web.admin import create_admin_app
from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.db.repositories.analytics import AnalyticsRepository
from tara_web.db.repositories.funding import FundingRepository


def _database(tmp_path: Path) -> ConnectionFactory:
    root = tmp_path / "runtime"
    backups = tmp_path / "backups"
    root.mkdir(mode=0o700)
    backups.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    connection = database.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    return database


def test_local_dashboard_shows_stats_kofi_and_applies_signed_adjustments(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    now = datetime.now(UTC).isoformat()
    analytics = AnalyticsRepository(database)
    analytics.record_page_view(page="new_job", day=datetime.now(UTC).date().isoformat())
    FundingRepository(database).record_kofi_event(
        message_id="test_payment_abcdefghijkl",
        event_type="Tip",
        amount_micros=5_000_000,
        currency="EUR",
        occurred_at=now,
        received_at=now,
        is_test_transaction=True,
    )
    with TestClient(create_admin_app(database, timezone="UTC")) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Nouvelle analyse" in page.text
        assert "Derniers webhooks Ko-fi" in page.text
        assert "<td>Oui</td>" in page.text
        assert "5,00 € EUR" in page.text
        assert page.headers["x-frame-options"] == "DENY"
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)
        assert csrf is not None
        token = csrf.group(1)
        added = client.post(
            "/consumption-adjustments",
            data={
                "csrf": token,
                "amount": "7.50",
                "note": "Correction locale",
                "direction": "add",
            },
            follow_redirects=False,
        )
        assert added.status_code == 303
        removed = client.post(
            "/consumption-adjustments",
            data={
                "csrf": token,
                "amount": "2.00",
                "note": "Réduction locale",
                "direction": "subtract",
            },
            follow_redirects=False,
        )
        assert removed.status_code == 303
        rejected = client.post(
            "/consumption-adjustments",
            data={
                "csrf": "wrong",
                "amount": "99.00",
                "note": "Refusé",
                "direction": "add",
            },
            follow_redirects=False,
        )
        assert rejected.headers["location"] == "/?saved=invalid"
        refreshed = client.get("/")
        assert "+5,50 €" in refreshed.text
        assert "Cumul public" in refreshed.text

    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    totals = FundingRepository(database).monthly_totals(
        start_utc=start.isoformat(), end_utc=end.isoformat()
    )
    assert totals.adjustment_micro_eur == 5_500_000
    assert totals.estimated_consumption_micro_eur == 5_500_000
