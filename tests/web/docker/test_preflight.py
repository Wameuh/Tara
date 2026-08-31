from __future__ import annotations

import json
import os
import runpy
from pathlib import Path

import pytest

_preflight = runpy.run_path(
    str(Path(__file__).parents[3] / "scripts/docker_preflight.py")
)
_private_writable_directory = _preflight["_private_writable_directory"]
_validate_inference_credentials = _preflight["_validate_inference_credentials"]
_validate_cursor_backend = _preflight["_validate_cursor_backend"]


def test_preflight_rejects_public_or_linked_volume(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    _private_writable_directory(private)
    if os.name != "nt":
        private.chmod(0o755)
        with pytest.raises(SystemExit, match="permissions"):
            _private_writable_directory(private)
        private.chmod(0o700)
    link = tmp_path / "link"
    try:
        link.symlink_to(private, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(SystemExit, match="invalid"):
        _private_writable_directory(link)


def _snapshot(provider: str, **transcription: str) -> str:
    return json.dumps(
        {
            "transcription": {
                "inference_auth_provider": provider,
                **transcription,
            }
        }
    )


def test_preflight_validates_only_selected_provider_credentials() -> None:
    _validate_inference_credentials(
        _snapshot("none"),
        {"MODAL_TOKEN_ID": "not-a-token"},
    )
    _validate_inference_credentials(
        _snapshot("modal_proxy"),
        {
            "TARA_MODAL_PROXY_AUTH_KEY": "wk-valid_proxy_key",
            "TARA_MODAL_PROXY_AUTH_SECRET": "ws-valid_proxy_secret",
        },
    )
    _validate_inference_credentials(
        _snapshot("modal_map"),
        {
            "MODAL_TOKEN_ID": "ak-valid_account_id",
            "MODAL_TOKEN_SECRET": "as-valid_account_secret",
        },
    )


@pytest.mark.parametrize(
    ("provider", "environment", "message"),
    [
        (
            "modal_proxy",
            {
                "TARA_MODAL_PROXY_AUTH_KEY": "ak-wrong_credential_type",
                "TARA_MODAL_PROXY_AUTH_SECRET": "as-wrong_credential_type",
            },
            "Modal proxy credentials",
        ),
        (
            "modal_map",
            {
                "MODAL_TOKEN_ID": "0123456789abcdef0123456789abcdef",
                "MODAL_TOKEN_SECRET": "0123456789abcdef0123456789abcdef",
            },
            "Modal API credentials",
        ),
        ("unknown", {}, "provider is unsupported"),
    ],
)
def test_preflight_rejects_credentials_for_the_wrong_provider(
    provider: str,
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(SystemExit, match=message):
        _validate_inference_credentials(_snapshot(provider), environment)


def test_preflight_honours_custom_modal_proxy_environment_names() -> None:
    snapshot = _snapshot(
        "modal_proxy",
        modal_proxy_key_env="CUSTOM_MODAL_KEY",
        modal_proxy_secret_env="CUSTOM_MODAL_SECRET",
    )
    _validate_inference_credentials(
        snapshot,
        {
            "CUSTOM_MODAL_KEY": "wk-custom_proxy_key",
            "CUSTOM_MODAL_SECRET": "ws-custom_proxy_secret",
        },
    )


def _cursor_snapshot(backend: str = "cursor_cli") -> str:
    return json.dumps(
        {
            "analysis": {
                "llm": {
                    "backend": backend,
                    "cursor_command": "cursor-agent",
                }
            }
        }
    )


def test_preflight_validates_cursor_runtime_without_reading_auth(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    auth = config_home / "cursor" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("opaque", encoding="utf-8")
    auth.chmod(0o600)
    available = {"cursor-agent": "/opt/cursor-agent/cursor-agent", "bwrap": "/usr/bin/bwrap"}

    _validate_cursor_backend(
        _cursor_snapshot(),
        {"XDG_CONFIG_HOME": str(config_home)},
        available.get,
    )


@pytest.mark.parametrize(
    ("available", "message"),
    [
        ({"bwrap": "/usr/bin/bwrap"}, "executable"),
        ({"cursor-agent": "/opt/cursor-agent/cursor-agent"}, "sandbox dependency"),
    ],
)
def test_preflight_rejects_incomplete_cursor_runtime(
    tmp_path: Path,
    available: dict[str, str],
    message: str,
) -> None:
    config_home = tmp_path / "config"
    auth = config_home / "cursor" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("opaque", encoding="utf-8")
    auth.chmod(0o600)

    with pytest.raises(SystemExit, match=message):
        _validate_cursor_backend(
            _cursor_snapshot(),
            {"XDG_CONFIG_HOME": str(config_home)},
            available.get,
        )


def test_preflight_rejects_missing_or_public_cursor_auth(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    available = {"cursor-agent": "/cursor-agent", "bwrap": "/bwrap"}
    with pytest.raises(SystemExit, match="authentication is unavailable"):
        _validate_cursor_backend(
            _cursor_snapshot(),
            {"XDG_CONFIG_HOME": str(config_home)},
            available.get,
        )

    auth = config_home / "cursor" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("opaque", encoding="utf-8")
    auth.chmod(0o644)
    with pytest.raises(SystemExit, match="authentication is invalid"):
        _validate_cursor_backend(
            _cursor_snapshot(),
            {"XDG_CONFIG_HOME": str(config_home)},
            available.get,
        )
