from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import load_config


def test_public_page_views_are_aggregated_without_visitor_data(tmp_path: Path) -> None:
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
""",
        encoding="utf-8",
    )
    with TestClient(create_app(load_config(config_path, {}))) as client:
        assert (
            client.post("/api/v1/metrics/page-view?page=new_job").status_code == 204
        )
        assert client.post("/api/v1/metrics/page-view?page=help").status_code == 204
        assert client.post("/api/v1/metrics/page-view?page=unknown").status_code == 422
        connection = client.app.state.database.connect()
        try:
            rows = connection.execute(
                "SELECT page,view_count FROM page_view_counts ORDER BY page"
            ).fetchall()
            assert [(row[0], row[1]) for row in rows] == [
                ("help", 1),
                ("new_job", 1),
            ]
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(page_view_counts)"
                ).fetchall()
            }
            assert columns == {"day", "page", "view_count"}
        finally:
            connection.close()
