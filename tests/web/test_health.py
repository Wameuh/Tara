from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import load_config


def test_technical_endpoints_are_stable_and_do_not_expose_paths(tmp_path: Path) -> None:
    config_file = tmp_path / "web.yaml"
    config_file.write_text(
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
    with TestClient(create_app(load_config(config_file))) as client:
        assert client.get("/api/v1/live").json() == {"status": "live"}
        assert client.get("/api/v1/ready").json() == {"status": "ready"}
        public = client.get("/api/v1/config/public").json()
        assert public == {
            "language": "fr",
            "locale": "fr-FR",
            "supported_languages": ["fr"],
            "max_upload_bytes": 1_073_741_824,
            "recommended_chunk_bytes": 1_048_576,
            "max_chunk_bytes": 8_388_608,
            "parallel_uploads": 3,
        }
        assert "runtime" not in str(public)


def test_openapi_is_off_by_default(tmp_path: Path) -> None:
    config_file = tmp_path / "web.yaml"
    config_file.write_text(
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
    with TestClient(create_app(load_config(config_file))) as client:
        assert client.get("/openapi.json").status_code == 404
