# ruff: noqa: ANN001
from __future__ import annotations

from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import load_config


def test_api_security_headers_and_origin(tmp_path) -> None:
    config = tmp_path / "web.yaml"
    config.write_text(
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
    with TestClient(create_app(load_config(config))) as client:
        response = client.get("/api/v1/live")
        csp = response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp
        assert "script-src 'self' 'wasm-unsafe-eval'" in csp
        assert "worker-src 'self'" in csp
        assert "'unsafe-eval'" not in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        denied = client.post(
            "/api/v1/uploads/sessions", headers={"Origin": "https://evil.invalid"}
        )
        assert denied.status_code == 403
