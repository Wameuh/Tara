from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
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
    assert service["pids_limit"]
    assert service["mem_limit"]
    assert service["cpus"]


def test_compose_topology_and_hardening_are_structured() -> None:
    compose = load_yaml(ROOT / "compose.yaml")
    services = compose["services"]
    assert set(services) == {"tara-web-init", "tara-web", "tara-proxy"}

    init = services["tara-web-init"]
    app = services["tara-web"]
    proxy = services["tara-proxy"]

    service_is_hardened(init)
    assert init["user"] == "0:0"
    assert init["network_mode"] == "none"
    assert init["cap_add"] == ["CHOWN", "DAC_OVERRIDE"]

    service_is_hardened(app)
    assert app["user"] == "10001:10001"
    assert app["init"] is True
    assert "ports" not in app
    assert app["networks"] == ["tara_internal", "tara_provider"]
    assert app["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert app["stop_grace_period"] == "45s"
    assert (
        app["depends_on"]["tara-web-init"]["condition"]
        == "service_completed_successfully"
    )
    health_command = " ".join(app["healthcheck"]["test"])
    assert "/api/v1/live" in health_command
    assert "/api/v1/ready" in health_command

    service_is_hardened(proxy)
    assert proxy["user"] == "101:101"
    assert proxy["ports"] == ["127.0.0.1:${TARA_WEB_HOST_PORT:-8080}:8080"]
    assert proxy["networks"] == ["tara_internal", "tara_public"]
    assert proxy["depends_on"]["tara-web"]["condition"] == "service_healthy"

    for service in services.values():
        for mount in service.get("tmpfs", []):
            assert mount.startswith("/")
            assert ":rw," in mount
            assert "noexec" in mount
            assert "nosuid" in mount
            assert "size=" in mount

    assert compose["networks"]["tara_internal"]["internal"] is True
    assert "tara_provider" not in proxy["networks"]
    assert "tara_provider" not in init.get("networks", [])


def test_compose_mounts_and_public_configuration_are_coherent() -> None:
    compose = load_yaml(ROOT / "compose.yaml")
    services = compose["services"]
    all_mounts = [
        str(mount)
        for service in services.values()
        for mount in service.get("volumes", [])
    ]
    forbidden = ("docker.sock", "/home", "../", "TaraRepo:/")
    assert not any(token in mount for mount in all_mounts for token in forbidden)

    bind_mounts = [mount for mount in all_mounts if mount.startswith("./")]
    assert set(bind_mounts) == {
        "./config/webinterface.yaml:/config/webinterface.yaml:ro",
        "./config/tara-web.yaml:/config/tara.yaml:ro",
        "./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro",
    }
    for mount in bind_mounts:
        source = mount.split(":", 1)[0]
        assert (ROOT / source).is_file()

    config = load_yaml(ROOT / "config/webinterface.yaml")["webinterface"]
    assert config["public_url"] == "http://localhost:8080"
    assert set(config["security"]["allowed_hosts"]) == {"localhost", "127.0.0.1"}
    assert config["security"]["allowed_origins"] == []

    serialized = repr(compose) + repr(config)
    assert "TARA_WEB_LINK_SECRET" not in serialized
    assert "link_secret" not in serialized


def test_dockerfile_build_gates_are_executable_and_runtime_is_minimal() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv:0.8.0" in dockerfile
    assert "uv sync --frozen --no-dev --extra deploy --no-install-project" in dockerfile
    assert "npm ci --ignore-scripts" in dockerfile
    assert "npm run lint && npm run test && npm run build" in dockerfile
    assert "npx --no-install openapi-typescript" in dockerfile
    assert "COPY webinterface/frontend/src/api/openapi.json" in dockerfile
    openapi_check = "/build/.venv/bin/python scripts/generate_web_openapi.py --check"
    i18n_check = "/build/.venv/bin/python scripts/generate_i18n_manifest.py --check"
    assert openapi_check in dockerfile
    assert i18n_check in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "tara_web.main"]' in dockerfile

    runtime = dockerfile.split("FROM python:3.13.1-slim-bookworm AS runtime", 1)[1]
    assert "COPY --from=python-build /build/src /app/src" in runtime
    assert "COPY --from=frontend-build" in runtime
    assert "tests" not in runtime
    assert "webinterface/frontend/src" not in runtime


def test_proxy_vite_and_smoke_contracts_are_present() -> None:
    nginx = (ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
    vite = (ROOT / "webinterface/frontend/vite.config.ts").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/smoke_web_compose.ps1").read_text(encoding="utf-8")

    assert "listen 8080" in nginx
    assert "proxy_pass http://tara-web:8000" in nginx
    assert 'host: "127.0.0.1"' in vite
    assert 'target: "http://127.0.0.1:8000"' in vite
    assert 'proxy: { "/api"' in vite

    for fragment in (
        "Invoke-DockerChecked",
        "Wait-Http",
        "Wait-ContainerStopped",
        "compose', 'config'",
        "ReadonlyRootfs",
        "PortBindings",
        "CapDrop",
        "SecurityOpt",
        "SIGTERM",
        "State.ExitCode",
        "@(0, 143)",
        "finally",
        "down', '-v', '--remove-orphans'",
    ):
        assert fragment in smoke
