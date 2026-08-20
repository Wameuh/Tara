from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import load_config


def test_private_markers_never_reach_logs_unauthorized_responses_or_plaintext_db(
    tmp_path: Path, caplog,
) -> None:
    context_marker = "PRIVATE_CONTEXT_MARKER_7d3f"
    summary_marker = "PRIVATE_SUMMARY_MARKER_92ac"
    config_path = tmp_path / "web.yaml"
    config_path.write_text(
        """webinterface:
  storage:
    root: ./runtime
    backups_root: ./backups
    sqlite_path: ./runtime/tara.sqlite3
  public_url: http://127.0.0.1:8000
  security:
    allowed_hosts: [127.0.0.1, testserver]
    allowed_origins: [http://127.0.0.1:8000]
""",
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO)
    app = create_app(load_config(config_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/uploads/sessions",
            headers={"Origin": "http://127.0.0.1:8000", "Idempotency-Key": "privacy-session"},
        )
        assert created.status_code == 201
        payload = created.json()
        secret = payload["secret"]
        session_id = payload["session_id"]
        updated = client.patch(
            f"/api/v1/sessions/{session_id}/inputs",
            headers={
                "Origin": "http://127.0.0.1:8000",
                "X-Tara-Job-Secret": secret,
                "Expected-Revision": str(payload["revision"]),
                "Idempotency-Key": "privacy-inputs",
            },
            json={
                "language": "fr",
                "context_text": context_marker,
                "previous_summaries_text": summary_marker,
            },
        )
        assert updated.status_code == 200
        denied = client.get(f"/api/v1/sessions/{session_id}")
        assert denied.status_code == 404
        assert context_marker not in denied.text
        assert summary_marker not in denied.text
        assert secret not in denied.text

    logs = caplog.text
    assert context_marker not in logs
    assert summary_marker not in logs
    assert secret not in logs
    assert str(tmp_path.resolve()) not in logs

    connection = app.state.database.connect()
    try:
        dump = "\n".join(connection.iterdump())
        assert secret not in dump
        assert "#secret=" not in dump
        paths = [row[0] for row in connection.execute("SELECT storage_path FROM upload_files")]
        assert all(not Path(value).is_absolute() and ".." not in Path(value).parts for value in paths)
        row = connection.execute(
            "SELECT context_text,previous_summaries_text FROM upload_sessions WHERE public_id=?",
            (session_id,),
        ).fetchone()
        assert tuple(row) == (context_marker, summary_marker)
    finally:
        connection.close()
