# ruff: noqa: E501
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import ConfigError, load_config


def write_config(tmp_path: Path, extra: str = "") -> Path:
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
"""
        + extra,
        encoding="utf-8",
    )
    return path


def test_lifespan_initializes_and_closes_sqlite(tmp_path: Path) -> None:
    app = create_app(load_config(write_config(tmp_path)))
    assert not hasattr(app.state, "sqlite")
    with TestClient(app) as client:
        assert client.get("/api/v1/ready").json() == {"status": "ready"}
        assert app.state.sqlite.execute("SELECT 1").fetchone() == (1,)
    assert app.state.ready is False


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Extra inputs"):
        load_config(write_config(tmp_path, "  unexpected: true\n"))


def test_required_secret_and_cross_validation_are_enforced(tmp_path: Path) -> None:
    path = write_config(tmp_path)
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content.replace("[127.0.0.1, testserver]", "[localhost, testserver]")
        + "    require_link_secret: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="public_url host"):
        load_config(path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "localhost, testserver", "127.0.0.1, testserver"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="required secret"):
        load_config(path, {})
    assert (
        load_config(path, {"TARA_WEB_LINK_SECRET": "test-only"}).link_secret is not None
    )


def test_catalogue_manifest_rejects_incomplete_default_and_removes_secondary(
    tmp_path: Path,
) -> None:
    from tara_web.catalogs import validate_catalogues

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"required_keys":["one"],"languages":{"fr":["one"],"en":[]}}',
        encoding="utf-8",
    )
    assert validate_catalogues(manifest, ("fr", "en"), "fr") == ("fr",)
    manifest.write_text(
        '{"required_keys":["one"],"languages":{"fr":[]}}', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="default language"):
        validate_catalogues(manifest, ("fr",), "fr")
