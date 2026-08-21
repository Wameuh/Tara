# ruff: noqa: ANN001, ANN202
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import load_config


def _config(tmp_path):
    path = tmp_path / "web.yaml"
    path.write_text(
        """webinterface:
  storage:
    root: ./runtime
    backups_root: ./backups
    sqlite_path: ./runtime/tara.sqlite3
  public_url: http://127.0.0.1:8000
  security:
    allowed_hosts: [127.0.0.1, testserver]
""",
        encoding="utf-8",
    )
    return load_config(path)


def _job(client: TestClient) -> tuple[str, str]:
    created = client.post("/api/v1/uploads/sessions", headers={"Idempotency-Key": "a"})
    session_id, secret = created.json()["session_id"], created.json()["secret"]
    database = client.app.state.database
    with database.transaction() as connection:
        session = connection.execute(
            "SELECT id,secret_hmac FROM upload_sessions WHERE public_id=?",
            (session_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,created_at,updated_at,expires_at,language) "
            "VALUES(?,?,?,'queued','v1',?,?,?,'fr')",
            (
                "job_abcdefghijklmnop",
                session["id"],
                session["secret_hmac"],
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            ),
        )
    return "job_abcdefghijklmnop", secret


def test_job_snapshot_requires_secret_and_is_no_store(tmp_path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        job_id, secret = _job(client)
        denied = client.get(f"/api/v1/jobs/{job_id}")
        assert denied.status_code == 404
        assert secret not in denied.text
        response = client.get(
            f"/api/v1/jobs/{job_id}", headers={"X-Tara-Job-Secret": secret}
        )
        assert response.status_code == 200
        assert set(response.json()["allowed_actions"]) == {
            "cancel",
            "regenerate_secret",
        }
        assert response.headers["cache-control"] == "no-store"


def test_secret_rotation_revokes_old_secret(tmp_path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        job_id, secret = _job(client)
        response = client.post(
            f"/api/v1/jobs/{job_id}/secret",
            headers={
                "X-Tara-Job-Secret": secret,
                "Idempotency-Key": "rotate",
                "Expected-Revision": "1",
            },
        )
        assert response.status_code == 200
        replacement = response.json()["secret"]
        assert replacement != secret and len(replacement) >= 43
        assert (
            client.get(
                f"/api/v1/jobs/{job_id}", headers={"X-Tara-Job-Secret": secret}
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/jobs/{job_id}", headers={"X-Tara-Job-Secret": replacement}
            ).status_code
            == 200
        )


def test_cancel_is_idempotent_with_expected_revision(tmp_path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        job_id, secret = _job(client)
        headers = {
            "X-Tara-Job-Secret": secret,
            "Idempotency-Key": "cancel-once",
            "Expected-Revision": "1",
        }
        assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers).json() == {
            "accepted": True
        }
        assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers).json() == {
            "accepted": True
        }
        snapshot = client.get(
            f"/api/v1/jobs/{job_id}", headers={"X-Tara-Job-Secret": secret}
        )
        assert snapshot.json()["status"] == "cancelled"


def test_session_inputs_are_canonical_persisted_and_revisioned(tmp_path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        created = client.post(
            "/api/v1/uploads/sessions", headers={"Idempotency-Key": "inputs"}
        ).json()
        session_id, secret = created["session_id"], created["secret"]
        response = client.patch(
            f"/api/v1/sessions/{session_id}/inputs",
            headers={"X-Tara-Job-Secret": secret, "Expected-Revision": "1", "Idempotency-Key": "inputs-once"},
            json={
                "language": "fr",
                "context_text": "Contexte persiste",
                "previous_summaries_text": "Resume persiste",
            },
        )
        assert response.status_code == 200
        assert response.json()["revision"] == 2
        assert response.json()["context_text"] == "Contexte persiste"
        assert response.json()["previous_summaries_text"] == "Resume persiste"
        legacy = client.get(
            f"/api/v1/uploads/sessions/{session_id}",
            headers={"X-Tara-Job-Secret": secret},
        ).json()
        assert "context_text" not in legacy


def test_session_inputs_accept_documented_maximum_text_sizes(tmp_path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        created = client.post(
            "/api/v1/uploads/sessions", headers={"Idempotency-Key": "large-inputs"}
        ).json()
        response = client.patch(
            f"/api/v1/sessions/{created['session_id']}/inputs",
            headers={
                "X-Tara-Job-Secret": created["secret"],
                "Expected-Revision": "1",
                "Idempotency-Key": "large-inputs-once",
            },
            json={
                "language": "fr",
                "context_text": "c" * 200_000,
                "previous_summaries_text": "s" * 2_000_000,
            },
        )
        assert response.status_code == 200
        assert response.json()["revision"] == 2
        assert len(response.json()["context_text"]) == 200_000
        assert len(response.json()["previous_summaries_text"]) == 2_000_000
