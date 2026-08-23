from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from stat import S_IXUSR
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def service_is_hardened(service: Mapping[str, Any]) -> None:
    assert service["read_only"] is True
    assert service["privileged"] is False
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["pids_limit"] and service["mem_limit"] and service["cpus"]
    assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_compose_topology_hardening_and_one_shot_services() -> None:
    compose = load_yaml(ROOT / "compose.yaml")
    services = compose["services"]
    assert set(services) == {
        "tara-web-init",
        "tara-web-migrate",
        "tara-web",
        "tara-admin",
        "tara-proxy",
        "tara-web-backup",
        "tara-web-restore",
    }
    for service in services.values():
        service_is_hardened(service)
        for tmpfs in service.get("tmpfs", []):
            assert tmpfs.startswith("/")
            assert ":rw,noexec,nosuid,size=" in tmpfs
    assert services["tara-web-init"]["cap_add"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
    ]
    app = services["tara-web"]
    assert app["user"] == "10001:10001"
    assert app["init"] is True
    assert "ports" not in app
    assert app["stop_grace_period"] == "90s"
    assert app["depends_on"]["tara-web-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert app["healthcheck"]["test"] == [
        "CMD",
        "python",
        "/app/docker/healthcheck.py",
    ]
    admin = services["tara-admin"]
    assert admin["user"] == "10001:10001"
    assert admin["ports"] == ["127.0.0.1:${TARA_ADMIN_HOST_PORT:-8765}:8765"]
    assert admin["networks"] == ["tara_admin"]
    assert admin["depends_on"]["tara-web-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert admin["healthcheck"]["test"] == [
        "CMD",
        "python",
        "/app/docker/admin_healthcheck.py",
    ]
    assert services["tara-web-migrate"]["network_mode"] == "none"
    assert services["tara-web-backup"]["profiles"] == ["operations"]
    assert services["tara-web-restore"]["profiles"] == ["restore"]
    proxy = services["tara-proxy"]
    assert proxy["ports"] == [
        "${TARA_WEB_BIND_ADDRESS:-127.0.0.1}:${TARA_WEB_HOST_PORT:-8443}:8443"
    ]
    assert proxy["networks"] == ["tara_internal", "tara_public"]
    assert compose["networks"]["tara_internal"]["internal"] is True
    assert compose["networks"]["tara_admin"]["driver"] == "bridge"


def test_compose_uses_dedicated_volumes_and_narrow_mounts() -> None:
    compose = load_yaml(ROOT / "compose.yaml")
    assert set(compose["volumes"]) == {
        "tara_web_jobs",
        "tara_web_db",
        "tara_web_backups",
        "tara_web_restore_jobs",
    }
    mounts = [
        str(mount)
        for service in compose["services"].values()
        for mount in service.get("volumes", [])
    ]
    assert not any(
        forbidden in mount
        for forbidden in ("docker.sock", "/home", "../", "TaraRepo:/")
        for mount in mounts
    )
    app_mounts = compose["services"]["tara-web"]["volumes"]
    assert "tara_web_jobs:/data/runtime" in app_mounts
    assert "tara_web_db:/data/runtime/db" in app_mounts
    assert "tara_web_backups:/data/backups" in app_mounts
    assert all(
        mount.endswith(":ro") for mount in app_mounts if str(mount).startswith("./")
    )


def test_compose_override_preserves_external_inference_configuration() -> None:
    rendered = (ROOT / "compose.override.yaml.example").read_text(encoding="utf-8")
    assert (
        "TARA_INFERENCE_ENDPOINT: "
        "${TARA_INFERENCE_ENDPOINT:-http://host.docker.internal:8000}"
    ) in rendered


def test_image_context_entrypoint_proxy_and_config_are_production_shaped() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    proxy = (ROOT / "docker/reverse-proxy/nginx.conf").read_text(encoding="utf-8")
    assert "node:22.23.2-bookworm-slim" in dockerfile
    assert dockerfile.count("@sha256:") == 4
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "npm ci --ignore-scripts" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "chown -R 0:0 /app" in dockerfile
    assert "chmod -R a=rX /app" in dockerfile
    assert "chown 10001:10001 /restore" in dockerfile
    assert "chmod 0700 /restore" in dockerfile
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert "tests" not in runtime and "webinterface/frontend/src" not in runtime
    for forbidden in (".env", "secrets", "*.jsonl", "tests", "docs"):
        assert forbidden in dockerignore
    assert "listen 8443 ssl" in proxy
    assert "ssl_protocols TLSv1.2 TLSv1.3" in proxy
    assert "proxy_buffering off" in proxy
    assert "X-Forwarded-For $remote_addr" in proxy
    assert "proxy_add_x_forwarded_for" not in proxy
    config = load_yaml(ROOT / "config/docker.example.yaml")["webinterface"]
    assert config["storage"]["sqlite_path"] == "/data/runtime/db/tara-web.sqlite3"
    assert config["public_url"].startswith("https://")
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")
    assert "load_secret TARA_MODAL_PROXY_AUTH_KEY" in entrypoint
    assert "load_secret TARA_MODAL_PROXY_AUTH_SECRET" in entrypoint
    assert "load_secret TARA_KOFI_VERIFICATION_TOKEN" in entrypoint
    assert "prepare_cursor_auth" in entrypoint
    assert 'chmod 600 "$cursor_config/auth.json"' in entrypoint
    tara_config = load_yaml(ROOT / "config/tara-web.yaml")
    assert tara_config["analysis"]["llm"]["backend"] == "cursor_cli"
    assert tara_config["analysis"]["llm"]["cursor_command"] == "cursor-agent"
    override = (ROOT / "compose.override.yaml.example").read_text(encoding="utf-8")
    assert (
        "TARA_KOFI_VERIFICATION_TOKEN_FILE: "
        "/run/secrets/kofi_verification_token"
    ) in override
    assert "TARA_CURSOR_AUTH_FILE: /run/secrets/cursor_auth" in override
    assert ":/opt/cursor-agent:ro" in override
    for script_name in ("docker-migrate.sh", "docker-backup.sh"):
        script = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "TARA_KOFI_VERIFICATION_TOKEN_FILE" in script
        assert "TARA_KOFI_VERIFICATION_TOKEN" in script


def test_operator_scripts_are_local_bounded_and_executable() -> None:
    expected = {
        "docker/entrypoint.sh",
        "docker/healthcheck.py",
        "docker/admin_healthcheck.py",
        "scripts/docker_preflight.py",
        "scripts/docker-migrate.sh",
        "scripts/docker-backup.sh",
        "scripts/docker-restore.sh",
        "scripts/smoke-web-compose.sh",
        "scripts/docker-sbom.sh",
        "scripts/source-security-reports.sh",
    }
    for relative in expected:
        path = ROOT / relative
        assert path.is_file()
        assert path.read_text(encoding="utf-8").startswith(("#!", '"""'))
        assert path.stat().st_mode & S_IXUSR
    restore = (ROOT / "scripts/docker-restore.sh").read_text(encoding="utf-8")
    assert "${#token}" in restore
    assert "*[!0-9a-f]*" in restore
    cli = (ROOT / "src/tara_web/cli.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in cli
    assert "    main()" in cli
