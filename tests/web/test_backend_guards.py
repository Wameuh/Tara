from __future__ import annotations

import importlib.resources
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.catalogs import validate_catalogues
from tara_web.config import ConfigError, load_config


def config_file(tmp_path: Path, replacement: str = "") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "web.yaml"
    content = """webinterface:
  storage:
    root: ./runtime
    backups_root: ./backups
    sqlite_path: ./runtime/tara.sqlite3
  public_url: http://127.0.0.1:8000
  security:
    allowed_hosts: [127.0.0.1, testserver, ::1]
  default_language: fr
  locale: fr-FR
  supported_languages: [fr]
"""
    path.write_text(content + replacement, encoding="utf-8")
    return path


@pytest.mark.parametrize("language", ["FR", "f", "fr_XX", "../fr", "fr-Fra"])
def test_invalid_default_language_is_rejected(tmp_path: Path, language: str) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "default_language: fr", f"default_language: {language}"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="language codes"):
        load_config(path)


@pytest.mark.parametrize("languages", ["[]", "[fr, fr]", "[fr, ../en]"])
def test_supported_languages_are_strict(tmp_path: Path, languages: str) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("[fr]", languages), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("replacement", ["locale: french", "locale: en-US"])
def test_locale_must_be_valid_and_match_default(
    tmp_path: Path, replacement: str
) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("locale: fr-FR", replacement),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="locale"):
        load_config(path)


@pytest.mark.parametrize("host", ["[]", '[" bad"]', "[*]", "[http://bad]"])
def test_invalid_allowed_hosts_are_rejected(tmp_path: Path, host: str) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("[127.0.0.1, testserver, ::1]", host),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="allowed_hosts"):
        load_config(path)


def test_ipv6_loopback_is_accepted(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    content = path.read_text(encoding="utf-8").replace(
        "http://127.0.0.1:8000", "http://[::1]:8000"
    )
    path.write_text(content, encoding="utf-8")
    assert load_config(path).web.public_url.host == "[::1]"


@pytest.mark.parametrize(
    "origin",
    [
        "http://user@example.test",
        "https://example.test/path",
        "https://example.test?a=b",
        "https://example.test#x",
    ],
)
def test_invalid_origins_are_rejected(tmp_path: Path, origin: str) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    allowed_hosts: [127.0.0.1, testserver, ::1]\n",
            "    allowed_hosts: [127.0.0.1, testserver, ::1]\n"
            f"    allowed_origins: [{origin}]\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="allowed_origins"):
        load_config(path)


def test_pure_origin_is_accepted(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    allowed_hosts: [127.0.0.1, testserver, ::1]\n",
            "    allowed_hosts: [127.0.0.1, testserver, ::1]\n"
            "    allowed_origins: [https://app.example:8443]\n",
        ),
        encoding="utf-8",
    )
    assert (
        str(load_config(path).web.security.allowed_origins[0])
        == "https://app.example:8443/"
    )


def test_https_remote_and_pure_origin_are_accepted(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    content = path.read_text(encoding="utf-8").replace(
        "http://127.0.0.1:8000", "https://tara.example"
    )
    content = content.replace("[127.0.0.1, testserver, ::1]", "[tara.example]")
    content = content.replace(
        "    allowed_hosts: [tara.example]",
        "    allowed_hosts: [tara.example]\n    allowed_origins: [https://app.example:8443]",
    )
    content += "  allow_fake_runner: true\n"
    path.write_text(content, encoding="utf-8")
    assert load_config(path).web.public_url.host == "tara.example"


def test_public_fake_runner_requires_explicit_opt_in(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    content = path.read_text(encoding="utf-8").replace(
        "http://127.0.0.1:8000", "https://tara.example"
    )
    content = content.replace("[127.0.0.1, testserver, ::1]", "[tara.example]")
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="fake runner"):
        load_config(path)


def test_tara_runner_requires_and_freezes_regular_configuration(
    tmp_path: Path,
) -> None:
    tara = tmp_path / "tara.yaml"
    tara.write_text(
        "analysis:\n  llm:\n    backend: deterministic\n",
        encoding="utf-8",
    )
    path = config_file(
        tmp_path,
        f"  runner_mode: tara\n  tara_config_path: {tara.name}\n",
    )

    loaded = load_config(path)

    assert loaded.tara_config_snapshot is not None
    snapshot = json.loads(loaded.tara_config_snapshot)
    assert snapshot["analysis"]["llm"]["backend"] == "deterministic"
    assert str(tara.resolve()) not in loaded.tara_config_snapshot


def test_tara_runner_rejects_missing_or_linked_configuration(tmp_path: Path) -> None:
    missing = config_file(
        tmp_path / "missing",
        "  runner_mode: tara\n  tara_config_path: missing.yaml\n",
    )
    with pytest.raises(ConfigError, match="Tara configuration"):
        load_config(missing)

    target = tmp_path / "tara.yaml"
    target.write_text("analysis: {}\n", encoding="utf-8")
    link = tmp_path / "tara-link.yaml"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    linked = config_file(
        tmp_path / "linked",
        f"  runner_mode: tara\n  tara_config_path: {link}\n",
    )
    with pytest.raises(ConfigError, match="Tara configuration"):
        load_config(linked)


def test_http_remote_and_storage_cross_checks_are_rejected(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("127.0.0.1:8000", "tara.example:8000")
        .replace("[127.0.0.1, testserver, ::1]", "[tara.example]"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="HTTPS"):
        load_config(path)
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "./runtime/tara.sqlite3", "./outside.sqlite3"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="inside storage.root"):
        load_config(path)


def test_default_absent_and_equal_storage_roots_are_rejected(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "supported_languages: [fr]", "supported_languages: [en]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="default_language"):
        load_config(path)
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("./backups", "./runtime"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="separate"):
        load_config(path)


def test_normalized_yaml_and_environment_paths_cannot_escape_root(
    tmp_path: Path,
) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "./runtime/tara.sqlite3", "./runtime/../outside.sqlite3"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="inside storage.root"):
        load_config(path)
    path = config_file(tmp_path)
    with pytest.raises(ConfigError, match="inside storage.root"):
        load_config(
            path,
            {
                "TARA_WEB_SQLITE_PATH": str(
                    tmp_path / "runtime" / ".." / "outside.sqlite3"
                )
            },
        )


@pytest.mark.parametrize(
    "variable",
    ["TARA_WEB_STORAGE_ROOT", "TARA_WEB_BACKUPS_ROOT", "TARA_WEB_SQLITE_PATH"],
)
def test_relative_environment_paths_are_rejected_and_absolute_accepted(
    tmp_path: Path, variable: str
) -> None:
    path = config_file(tmp_path)
    with pytest.raises(ConfigError, match="absolute"):
        load_config(path, {variable: "relative"})
    environment = {variable: str(tmp_path / "runtime" / "state.sqlite3")}
    if variable == "TARA_WEB_STORAGE_ROOT":
        environment[variable] = str(tmp_path / "runtime")
    if variable == "TARA_WEB_BACKUPS_ROOT":
        environment[variable] = str(tmp_path / "backups-absolute")
    assert load_config(path, environment).web.storage


def test_unknown_top_level_invalid_yaml_and_required_secret(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    path.write_text("unknown: true", encoding="utf-8")
    with pytest.raises(ConfigError, match="top-level"):
        load_config(path)
    path.write_text("webinterface: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid web configuration YAML"):
        load_config(path)
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    allowed_hosts: [127.0.0.1, testserver, ::1]\n",
            "    allowed_hosts: [127.0.0.1, testserver, ::1]\n"
            "    require_link_secret: true\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="required secret"):
        load_config(path, {})
    assert load_config(path, {"TARA_WEB_LINK_SECRET": "value"}).link_secret


def write_manifest(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"languages": {}},
        {"required_keys": []},
        {"required_keys": ["x", "x"], "languages": {}},
        {"required_keys": [1], "languages": {}},
    ],
)
def test_invalid_manifest_shapes_are_actionable(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigError, match="manifest is invalid"):
        validate_catalogues(write_manifest(tmp_path / "m.json", value), ("fr",), "fr")


def test_manifest_validates_real_entries_and_removes_secondary(tmp_path: Path) -> None:
    manifest = write_manifest(
        tmp_path / "m.json",
        {"required_keys": ["a", "b"], "languages": {"fr": ["a", "b"], "en": ["a"]}},
    )
    assert validate_catalogues(manifest, ("fr", "en"), "fr") == ("fr",)
    with pytest.raises(ConfigError, match="default language"):
        validate_catalogues(manifest, ("en",), "en")


@pytest.mark.parametrize(
    "languages",
    [{"fr": "not-list"}, {"fr": ["a", "a"]}, {"../fr": ["a"]}, {"fr": [1]}],
)
def test_manifest_rejects_bad_language_entries(
    tmp_path: Path, languages: object
) -> None:
    manifest = write_manifest(
        tmp_path / "m.json", {"required_keys": ["a"], "languages": languages}
    )
    with pytest.raises(ConfigError):
        validate_catalogues(manifest, ("fr",), "fr")


def test_ready_is_dynamic_and_live_stays_live(tmp_path: Path) -> None:
    app = create_app(load_config(config_file(tmp_path)))
    with TestClient(app) as client:
        app.state.draining = True
        assert client.get("/api/v1/live").status_code == 200
        assert client.get("/api/v1/ready").status_code == 503
        app.state.draining = False
        app.state.sqlite.close()
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert str(tmp_path) not in response.text


def test_ready_rejects_missing_or_replaced_backup_root(tmp_path: Path) -> None:
    app = create_app(load_config(config_file(tmp_path)))
    with TestClient(app) as client:
        backup = app.state.runtime_config.web.storage.backups_root
        backup.rmdir()
        assert client.get("/api/v1/ready").status_code == 503
    app = create_app(load_config(config_file(tmp_path / "second")))
    with TestClient(app) as client:
        backup = app.state.runtime_config.web.storage.backups_root
        backup.rmdir()
        backup.write_text("not a directory", encoding="utf-8")
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert "not a directory" not in response.text


def test_ready_rejects_root_replaced_by_file(tmp_path: Path) -> None:
    app = create_app(load_config(config_file(tmp_path)))
    with TestClient(app) as client:
        root = app.state.runtime_config.web.storage.root
        app.state.sqlite.close()
        for child in root.iterdir():
            child.unlink()
        root.rmdir()
        root.write_text("not a directory", encoding="utf-8")
        assert client.get("/api/v1/ready").status_code == 503


def test_upload_origin_guard_accepts_same_origin_and_rejects_third_party(
    tmp_path: Path,
) -> None:
    app = create_app(load_config(config_file(tmp_path)))
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/uploads/sessions",
                headers={"Origin": "http://127.0.0.1:8000", "Idempotency-Key": "same"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/v1/uploads/sessions",
                headers={"Origin": "https://third.example", "Idempotency-Key": "third"},
            ).status_code
            == 403
        )


def test_upload_idempotency_replay_never_bypasses_secret(tmp_path: Path) -> None:
    app = create_app(load_config(config_file(tmp_path)))
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/uploads/sessions", headers={"Idempotency-Key": "create"}
        ).json()
        body = {
            "filename": "Alice.mp3",
            "size": 4,
            "sha256": "a" * 64,
            "mime": "audio/mpeg",
        }
        headers = {
            "Idempotency-Key": "declare",
            "X-Tara-Job-Secret": session["secret"],
        }
        assert (
            client.post(
                f"/api/v1/uploads/sessions/{session['session_id']}/files",
                headers=headers,
                json=body,
            ).status_code
            == 201
        )
        for secret in (None, "wrong"):
            replay_headers = {"Idempotency-Key": "declare"}
            if secret:
                replay_headers["X-Tara-Job-Secret"] = secret
            assert (
                client.post(
                    f"/api/v1/uploads/sessions/{session['session_id']}/files",
                    headers=replay_headers,
                    json=body,
                ).status_code
                == 404
            )


def test_embedded_manifest_publishes_only_complete_languages(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "supported_languages: [fr]", "supported_languages: [fr, en]"
        ),
        encoding="utf-8",
    )
    with TestClient(create_app(load_config(path))) as client:
        public = client.get("/api/v1/config/public").json()
        assert public["supported_languages"] == ["fr", "en"]
        assert set(public) == {
            "language",
            "locale",
            "supported_languages",
            "max_upload_bytes",
            "recommended_chunk_bytes",
            "max_chunk_bytes",
            "parallel_uploads",
        }


def test_spa_fallback_and_api_separation(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("INDEX", encoding="utf-8")
    (dist / "favicon.ico").write_text("ICON", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("ASSET", encoding="utf-8")
    with TestClient(create_app(load_config(config_file(tmp_path)), dist)) as client:
        assert client.get("/").text == "INDEX"
        assert client.get("/jobs/opaque").text == "INDEX"
        assert client.get("/favicon.ico").text == "ICON"
        assert client.get("/assets/app.js").text == "ASSET"
        assert client.get("/assets/missing.js").status_code == 404
        for route in ("/api", "/api/unknown", "/api/v1/unknown"):
            response = client.get(route)
            assert response.status_code == 404 and "INDEX" not in response.text
        outside = tmp_path / "outside.txt"
        outside.write_text("PRIVATE", encoding="utf-8")
        assert "PRIVATE" not in client.get("/../outside.txt").text


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_posix_sensitive_permissions_are_rejected(tmp_path: Path) -> None:
    from tara_web.app import _validate_sensitive_permissions

    tmp_path.chmod(0o777)
    with pytest.raises(RuntimeError, match="sensitive path"):
        _validate_sensitive_permissions(tmp_path)
    tmp_path.chmod(0o600)
    with pytest.raises(RuntimeError, match="sensitive path"):
        _validate_sensitive_permissions(tmp_path)


def test_embedded_manifest_is_packaged_and_readable() -> None:
    manifest = importlib.resources.files("tara_web").joinpath("i18n_manifest.json")
    assert json.loads(manifest.read_text(encoding="utf-8"))["languages"]["fr"]
