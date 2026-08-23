from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import RuntimeConfig, load_config

TOKEN = "test-kofi-verification-token"


def _config(tmp_path: Path, *, enabled: bool = True) -> RuntimeConfig:
    path = tmp_path / "web.yaml"
    path.write_text(
        f"""webinterface:
  storage:
    root: ./runtime
    backups_root: ./backups
    sqlite_path: ./runtime/tara.sqlite3
  public_url: http://127.0.0.1:8000
  security:
    allowed_hosts: [127.0.0.1, testserver]
  kofi:
    enabled: {str(enabled).lower()}
    page_url: https://ko-fi.com/tara
    monthly_goal_micro_eur: 50000000
    timezone: UTC
""",
        encoding="utf-8",
    )
    environment = {
        "TARA_KOFI_VERIFICATION_TOKEN": TOKEN if enabled else ""
    }
    return load_config(path, environment)


def _webhook_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "verification_token": TOKEN,
        "message_id": "payment_abcdefghijklmnop",
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "Tip",
        "is_public": False,
        "amount": "4.50",
        "currency": "EUR",
        "from_name": "Private supporter",
        "email": "private@example.invalid",
        "message": "Private message",
    }
    payload.update(changes)
    return payload


def _post_webhook(
    client: TestClient, payload: dict[str, object]
) -> httpx.Response:
    body = urlencode({"data": json.dumps(payload)})
    return client.post(
        "/api/v1/funding/kofi/webhook",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def test_webhook_is_verified_idempotent_and_privacy_preserving(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert _post_webhook(client, _webhook_payload()).status_code == 200
        assert _post_webhook(client, _webhook_payload()).status_code == 200
        snapshot = client.get("/api/v1/funding/monthly")
        assert snapshot.status_code == 200
        assert snapshot.json() == {
            "enabled": True,
            "month": datetime.now(UTC).strftime("%Y-%m"),
            "timezone": "UTC",
            "currency": "EUR",
            "donations_micro_eur": 4_500_000,
            "estimated_consumption_micro_eur": 0,
            "estimate_partial": False,
            "monthly_goal_micro_eur": 50_000_000,
            "kofi_page_url": "https://ko-fi.com/tara",
        }
        connection = client.app.state.database.connect()
        try:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(kofi_payment_events)"
                ).fetchall()
            }
            assert {"from_name", "email", "message"}.isdisjoint(columns)
            assert connection.execute(
                "SELECT COUNT(*) FROM kofi_payment_events"
            ).fetchone()[0] == 1
        finally:
            connection.close()


def test_webhook_rejects_wrong_token_and_excludes_shop_and_other_currency(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert (
            _post_webhook(client, _webhook_payload(verification_token="wrong-token"))
            .status_code
            == 403
        )
        assert _post_webhook(
            client,
            _webhook_payload(message_id="shop_abcdefghijklmnop", type="Shop Order"),
        ).status_code == 200
        assert _post_webhook(
            client,
            _webhook_payload(message_id="usd_abcdefghijklmnop", currency="USD"),
        ).status_code == 200
        assert _post_webhook(
            client,
            _webhook_payload(
                message_id="test_abcdefghijklmnop", is_test_transaction=True
            ),
        ).status_code == 200
        assert (
            client.get("/api/v1/funding/monthly").json()["donations_micro_eur"]
            == 0
        )
        connection = client.app.state.database.connect()
        try:
            test_event = connection.execute(
                "SELECT amount_micros,is_test_transaction "
                "FROM kofi_payment_events WHERE message_id=?",
                ("test_abcdefghijklmnop",),
            ).fetchone()
            assert tuple(test_event) == (4_500_000, 1)
        finally:
            connection.close()


def test_webhook_counts_subscription_and_legacy_donation(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert _post_webhook(
            client,
            _webhook_payload(
                message_id="subscription_abcdefghijkl",
                type="Subscription",
                amount="5.00",
            ),
        ).status_code == 200
        assert _post_webhook(
            client,
            _webhook_payload(
                message_id="donation_abcdefghijklmnop",
                type="Donation",
                amount="2.50",
            ),
        ).status_code == 200
        assert (
            client.get("/api/v1/funding/monthly").json()["donations_micro_eur"]
            == 7_500_000
        )


def test_monthly_consumption_is_the_sum_of_tara_estimates(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        now = datetime.now(UTC)
        with client.app.state.database.transaction() as connection:
            connection.execute(
                "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
                "created_at,updated_at) VALUES(?,?,'consumed',?,?,?)",
                (
                    "session_abcdefghijklmnop",
                    "v1:" + "a" * 64,
                    (now + timedelta(days=1)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
                "pipeline_version,created_at,updated_at,expires_at,language) "
                "VALUES(?,1,?,'running','v1',?,?,?,'fr')",
                (
                    "job_abcdefghijklmnop",
                    "v1:" + "a" * 64,
                    now.isoformat(),
                    now.isoformat(),
                    (now + timedelta(days=1)).isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO job_attempts(job_id,attempt_number,status,started_at) "
                "VALUES(1,1,'running',?)",
                (now.isoformat(),),
            )
            common = (
                1,
                1,
                "analysis",
                "cursor",
                "success",
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            )
            connection.execute(
                "INSERT INTO provider_usage_attempts(attempt_id,job_id,"
                "job_attempt_number,operation_family,provider,status,started_at,"
                "finished_at,input_tokens,output_tokens,cache_tokens,duration_ms,"
                "cost_micro_eur,cost_source,native_cost_micros,native_currency,"
                "conversion_rate,created_at) VALUES(?,?,?,?,?,?,?,?,0,0,0,0,?,"
                "'estimated',?,'EUR','1',?)",
                ("usage_known", *common[:7], 1_250_000, 1_250_000, common[7]),
            )
            connection.execute(
                "INSERT INTO provider_usage_attempts(attempt_id,job_id,"
                "job_attempt_number,operation_family,provider,status,started_at,"
                "finished_at,input_tokens,output_tokens,cache_tokens,duration_ms,"
                "cost_micro_eur,cost_source,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,0,0,0,0,NULL,'unavailable',?)",
                ("usage_unknown", *common[:7], common[7]),
            )
        snapshot = client.get("/api/v1/funding/monthly").json()
        assert snapshot["estimated_consumption_micro_eur"] == 1_250_000
        assert snapshot["estimate_partial"] is True


def test_disabled_integration_is_publicly_inactive(tmp_path: Path) -> None:
    disabled = _config(tmp_path, enabled=False)
    with TestClient(create_app(disabled)) as client:
        snapshot = client.get("/api/v1/funding/monthly").json()
        assert snapshot["enabled"] is False
        assert _post_webhook(client, _webhook_payload()).status_code == 404
