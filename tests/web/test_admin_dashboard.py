from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
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


def test_dashboard_accepts_only_configured_loopback_host(tmp_path: Path) -> None:
    database = _database(tmp_path)
    app = create_admin_app(
        database,
        timezone="UTC",
        allowed_hosts=("127.0.0.1", "localhost", "testserver"),
    )
    with TestClient(app) as client:
        accepted = client.get("/health", headers={"host": "127.0.0.1"})
        rejected = client.get("/health", headers={"host": "attacker.example"})
        assert accepted.status_code == 200
        assert rejected.status_code == 400


def test_non_loopback_admin_requires_auth(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(ValueError, match="strong password"):
        create_admin_app(
            database,
            timezone="UTC",
            allowed_hosts=("192.168.1.109", "testserver"),
        )

    password = "strong-admin-password-for-tests"
    app = create_admin_app(
        database,
        timezone="UTC",
        allowed_hosts=("192.168.1.109", "testserver"),
        admin_password=password,
    )
    token = base64.b64encode(f"tara-admin:{password}".encode()).decode()
    with TestClient(app) as client:
        denied = client.get("/", headers={"host": "192.168.1.109"})
        accepted = client.get(
            "/",
            headers={
                "host": "192.168.1.109",
                "Authorization": f"Basic {token}",
            },
        )
        assert denied.status_code == 401
        assert accepted.status_code == 200


def test_optional_basic_auth_protects_loopback_dashboard(tmp_path: Path) -> None:
    database = _database(tmp_path)
    password = "strong-admin-password-for-tests"
    app = create_admin_app(
        database,
        timezone="UTC",
        allowed_hosts=("127.0.0.1", "testserver"),
        admin_password=password,
    )
    token = base64.b64encode(f"tara-admin:{password}".encode()).decode()
    with TestClient(app) as client:
        assert client.get("/health", headers={"host": "127.0.0.1"}).status_code == 200
        denied = client.get("/", headers={"host": "127.0.0.1"})
        accepted = client.get(
            "/",
            headers={
                "host": "127.0.0.1",
                "Authorization": f"Basic {token}",
            },
        )
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Basic ")
    assert accepted.status_code == 200


def test_dashboard_lists_recent_failures_with_stage_reason_and_code(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES(?,?, 'consumed', ?, ?, ?)",
            (
                "us_admin_failure_0001",
                "v1:" + "a" * 64,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "error_code,pipeline_version,stage,created_at,updated_at,finished_at,"
            "expires_at) VALUES(?,1,?,'failed','transcription_failed','v1',"
            "'transcription',?,?,?,?)",
            ("job_admin_failure_0001", "v1:" + "a" * 64, now, now, now, now),
        )
        connection.execute(
            "INSERT INTO job_run_events(job_id,attempt_number,event_revision,"
            "event_type,payload_json,created_at) VALUES(1,1,1,'run_failed',?,?)",
            (
                json.dumps(
                    {
                        "code": "transcription_failed",
                        "detail": "secret provider diagnostic",
                    }
                ),
                "2026-01-15T12:34:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO job_run_events(job_id,attempt_number,event_revision,"
            "event_type,payload_json,created_at) VALUES(1,1,2,'warning_raised',?,?)",
            (
                json.dumps({"code": "secret code from provider"}),
                "2026-01-15T12:35:00+00:00",
            ),
        )

    with TestClient(create_admin_app(database, timezone="Europe/Helsinki")) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert "Dernières analyses échouées" in page.text
    assert "job_admin_failure_0001" in page.text
    assert "Échouée" in page.text
    assert "Transcription" in page.text
    assert "Moteur de transcription indisponible ou en erreur" in page.text
    assert "<code>transcription_failed</code>" in page.text
    assert "Journal technique" in page.text
    assert "2026-01-15 14:34 EET" in page.text
    assert "Tentative 1" in page.text
    assert "Échec du traitement" in page.text
    assert "secret provider diagnostic" not in page.text
    assert "secret code from provider" not in page.text
    assert "cause_non_renseignee" in page.text
