"""Strict, explicit configuration for the Tara web process."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import asdict
from ipaddress import ip_network
from json import dumps
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML

from tara.config import load_config as load_tara_config
from tara.web_contracts import MAX_TARA_CONFIG_SNAPSHOT_BYTES

LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
HOST_NAME = re.compile(r"^[A-Za-z0-9.-]+$")


class ConfigError(ValueError):
    """Raised with an operator-actionable configuration error."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StorageConfig(StrictModel):
    root: Path
    backups_root: Path
    sqlite_path: Path


class LimitsConfig(StrictModel):
    max_json_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    rate_limit_global: int = Field(default=240, ge=1, le=100_000)
    rate_limit_invalid_secret: int = Field(default=30, ge=1, le=100_000)
    rate_limit_public_creation: int = Field(default=30, ge=1, le=100_000)
    rate_limit_expensive_command: int = Field(default=60, ge=1, le=100_000)
    rate_limit_polling: int = Field(default=120, ge=1, le=100_000)
    rate_limit_sse_open: int = Field(default=20, ge=1, le=100_000)
    rate_limit_upload_chunk: int = Field(default=2_000, ge=1, le=100_000)
    rate_limit_upload_chunk_global: int = Field(default=10_000, ge=1, le=100_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    max_upload_bytes: int = Field(default=1_073_741_824, gt=0)
    max_merged_transcription_bytes: int = Field(
        default=32 * 1024 * 1024, ge=1_024, le=128 * 1024 * 1024
    )
    max_merged_transcription_tokens: int = Field(
        default=500_000, ge=1, le=2_000_000
    )
    merged_validation_timeout_seconds: int = Field(default=15, ge=1, le=120)
    merged_validation_memory_bytes: int = Field(
        default=512 * 1024 * 1024, ge=128 * 1024 * 1024, le=4 * 1024**3
    )
    merged_yaml_max_depth: int = Field(default=64, ge=4, le=128)
    merged_yaml_max_nodes: int = Field(default=1_000_000, ge=100, le=2_000_000)
    merged_yaml_max_scalar_chars: int = Field(
        default=32 * 1024 * 1024, ge=1_024, le=64 * 1024 * 1024
    )
    merged_yaml_max_aliases: int = Field(default=0, ge=0, le=16)
    zip_max_entries: int = Field(default=1_000, ge=1, le=100_000)
    zip_max_uncompressed_bytes: int = Field(
        default=5 * 1_073_741_824, ge=1, le=10**15
    )
    zip_max_compression_ratio: float = Field(default=100.0, ge=1.0, le=10_000.0)
    zip_max_depth: int = Field(default=16, ge=1, le=128)
    zip_validation_timeout_seconds: int = Field(default=30, ge=1, le=600)
    max_active_jobs: int = Field(default=5, ge=1, le=32)
    max_waiting_jobs: int = Field(default=25, ge=0, le=10_000)
    max_ipc_messages: int = Field(default=512, ge=16, le=16_384)
    job_timeout_seconds: int = Field(default=3_600, ge=1, le=86_400)
    cancellation_grace_seconds: int = Field(default=15, ge=1, le=600)
    max_sse_connections: int = Field(default=100, ge=1, le=10_000)
    max_sse_connections_per_job: int = Field(default=4, ge=1, le=100)
    sse_heartbeat_seconds: int = Field(default=15, ge=5, le=300)
    max_artifact_bytes: int = Field(default=1_073_741_824, ge=1, le=10**15)
    min_free_storage_bytes: int = Field(default=268_435_456, ge=0, le=10**15)
    max_artifacts_per_job: int = Field(default=64, ge=1, le=10_000)
    storage_cleanup_batch_size: int = Field(default=100, ge=1, le=10_000)
    storage_orphan_grace_seconds: int = Field(default=3600, ge=3600, le=31_536_000)
    storage_maintenance_interval_seconds: int = Field(default=3600, ge=1, le=86_400)
    max_chunk_bytes: int = Field(default=8_388_608, ge=16_384, le=67_108_864)
    recommended_chunk_bytes: int = Field(default=1_048_576, ge=16_384, le=67_108_864)
    max_upload_files: int = Field(default=1_000, ge=1, le=100_000)
    max_upload_sessions: int = Field(default=100, ge=1, le=10_000)
    max_reserved_upload_bytes: int = Field(default=10_737_418_240, ge=1)
    upload_read_timeout_seconds: int = Field(default=30, ge=1, le=600)
    upload_inactivity_seconds: int = Field(default=1800, ge=60, le=86_400)
    upload_session_retention_hours: int = Field(default=24, ge=1, le=168)
    validation_concurrency: int = Field(default=2, ge=1, le=16)
    public_parallel_uploads: int = Field(default=3, ge=1, le=16)
    ffprobe_timeout_seconds: int = Field(default=30, ge=1, le=600)
    ffmpeg_timeout_seconds: int = Field(default=60, ge=1, le=600)
    scanner_timeout_seconds: int = Field(default=60, ge=1, le=600)
    inference_budget_micro_eur: int | None = Field(default=None, ge=0, le=10**15)
    inference_reservation_micro_eur: int = Field(
        default=1_000_000, ge=0, le=10**15
    )
    estimation_minimum_observations: int = Field(default=8, ge=2, le=10_000)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=1_000)
    circuit_open_seconds: int = Field(default=60, ge=1, le=86_400)

    @model_validator(mode="after")
    def reservation_fits_budget(self) -> LimitsConfig:
        if (
            self.inference_budget_micro_eur is not None
            and self.inference_reservation_micro_eur
            > self.inference_budget_micro_eur
        ):
            raise ValueError("inference reservation must fit the global budget")
        return self


class TimeoutsConfig(StrictModel):
    shutdown_grace_seconds: int = Field(default=30, ge=1, le=3_600)
    request_seconds: int = Field(default=60, ge=1, le=3_600)


class DocumentationConfig(StrictModel):
    enabled: bool = False


class BackupConfig(StrictModel):
    """Controlled-shutdown backup policy.

    The signing key deliberately lives in the environment and is never part of
    the serialised web configuration or the backup itself.
    """

    enabled: bool = False
    signing_key_env: str = "TARA_WEB_BACKUP_SIGNING_KEY"

    @field_validator("signing_key_env")
    @classmethod
    def signing_environment_name_is_safe(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum() or value != value.upper():
            raise ValueError(
                "backup signing_key_env must be an uppercase environment variable"
            )
        return value


class KoFiConfig(StrictModel):
    """Public funding display and private Ko-fi webhook settings."""

    enabled: bool = False
    page_url: HttpUrl | None = None
    verification_token_env: str = "TARA_KOFI_VERIFICATION_TOKEN"
    monthly_goal_micro_eur: int | None = Field(default=None, ge=1, le=10**15)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("verification_token_env")
    @classmethod
    def token_environment_name_is_safe(cls, value: str) -> str:
        if not value.replace("_", "").isalnum() or value != value.upper():
            raise ValueError(
                "Ko-fi verification_token_env must be an uppercase environment variable"
            )
        return value

    @field_validator("page_url")
    @classmethod
    def page_is_a_secure_kofi_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        host = (value.host or "").lower()
        if value.scheme != "https" or host not in {"ko-fi.com", "www.ko-fi.com"}:
            raise ValueError("Ko-fi page_url must be an HTTPS ko-fi.com URL")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Ko-fi timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def enabled_configuration_is_complete(self) -> KoFiConfig:
        if self.enabled and self.page_url is None:
            raise ValueError("enabled Ko-fi integration requires page_url")
        return self


class SecurityConfig(StrictModel):
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1", "testserver")
    allowed_origins: tuple[HttpUrl, ...] = ()
    link_secret_env: str = "TARA_WEB_LINK_SECRET"
    require_link_secret: bool = False
    upload_hmac_env: str = "TARA_WEB_UPLOAD_HMAC_KEY"
    trusted_proxy_networks: tuple[str, ...] = ()
    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0, le=63_072_000)
    hsts_include_subdomains: bool = False

    @field_validator("allowed_hosts")
    @classmethod
    def hosts_are_concrete(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(
            not host
            or host != host.strip()
            or (host != "::1" and not HOST_NAME.fullmatch(host))
            for host in value
        ):
            raise ValueError("allowed_hosts must be a non-empty explicit allow-list")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def origins_are_origins(cls, value: tuple[HttpUrl, ...]) -> tuple[HttpUrl, ...]:
        for origin in value:
            if (
                origin.username
                or origin.password
                or origin.path not in ("", "/")
                or origin.query
                or origin.fragment
            ):
                raise ValueError(
                    "allowed_origins must contain scheme, host and optional port only"
                )
        return value

    @field_validator("link_secret_env")
    @classmethod
    def secret_environment_name_is_safe(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum() or value != value.upper():
            raise ValueError(
                "link_secret_env must be an uppercase environment variable"
            )
        return value

    @field_validator("trusted_proxy_networks")
    @classmethod
    def proxy_networks_are_cidrs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        try:
            if any(str(ip_network(item, strict=True)) != item for item in value):
                raise ValueError
        except ValueError as exc:
            raise ValueError("trusted_proxy_networks must be explicit CIDRs") from exc
        return value


class WebinterfaceConfig(StrictModel):
    storage: StorageConfig
    public_url: HttpUrl = "http://127.0.0.1:8000"
    security: SecurityConfig = SecurityConfig()
    limits: LimitsConfig = LimitsConfig()
    timeouts: TimeoutsConfig = TimeoutsConfig()
    default_language: str = "fr"
    locale: str = "fr-FR"
    supported_languages: tuple[str, ...] = ("fr",)
    workers: int = Field(default=1, ge=1, le=32)
    documentation: DocumentationConfig = DocumentationConfig()
    backup: BackupConfig = BackupConfig()
    kofi: KoFiConfig = KoFiConfig()
    runner_mode: str = "fake"
    tara_config_path: Path | None = None
    allow_fake_runner: bool = False

    @field_validator("runner_mode")
    @classmethod
    def runner_mode_is_server_controlled(cls, value: str) -> str:
        if value not in {"fake", "tara"}:
            raise ValueError("runner_mode must be fake or tara")
        return value

    @field_validator("default_language")
    @classmethod
    def language_code(cls, value: str) -> str:
        if not LANGUAGE_TAG.fullmatch(value):
            raise ValueError("language codes must use safe ASCII BCP-47 tags")
        return value

    @field_validator("supported_languages")
    @classmethod
    def languages_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("supported_languages must be a non-empty unique list")
        if any(not LANGUAGE_TAG.fullmatch(language) for language in value):
            raise ValueError("language codes must use safe ASCII BCP-47 tags")
        return value

    @model_validator(mode="after")
    def cross_validate(self) -> WebinterfaceConfig:
        if self.default_language not in self.supported_languages:
            raise ValueError("default_language must be included in supported_languages")
        if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", self.locale):
            raise ValueError("locale must use a safe language-region tag")
        if self.locale.split("-", 1)[0] != self.default_language:
            raise ValueError("locale language must match default_language")
        public_parts = urlparse(str(self.public_url))
        public_host = public_parts.hostname
        if public_host not in self.security.allowed_hosts:
            raise ValueError("public_url host must be listed in security.allowed_hosts")
        if public_parts.scheme != "https" and public_host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("public_url must use HTTPS outside loopback")
        if (
            self.runner_mode == "fake"
            and public_host not in {"localhost", "127.0.0.1", "::1"}
            and not self.allow_fake_runner
        ):
            raise ValueError("fake runner requires explicit public deployment opt-in")
        if self.runner_mode == "tara" and self.tara_config_path is None:
            raise ValueError("tara runner requires tara_config_path")
        sqlite_path = self.storage.sqlite_path.resolve(strict=False)
        storage_root = self.storage.root.resolve(strict=False)
        if not sqlite_path.is_relative_to(storage_root):
            raise ValueError("storage.sqlite_path must stay inside storage.root")
        if self.storage.root == self.storage.backups_root:
            raise ValueError("storage.root and storage.backups_root must be separate")
        backup_root = self.storage.backups_root.resolve(strict=False)
        if backup_root.is_relative_to(storage_root) or storage_root.is_relative_to(
            backup_root
        ):
            raise ValueError(
                "storage.root and storage.backups_root must be independent trees"
            )
        return self


class RuntimeConfig(StrictModel):
    """Immutable runtime view; secrets are intentionally absent from public models."""

    web: WebinterfaceConfig
    link_secret: SecretStr | None = None
    upload_hmac_key: SecretStr | None = None
    backup_signing_key: SecretStr | None = None
    kofi_verification_token: SecretStr | None = None
    tara_config_snapshot: str | None = None


def load_config(
    config_path: Path, environ: Mapping[str, str] | None = None
) -> RuntimeConfig:
    """Load one explicit YAML file and apply documented operational overrides."""

    if not config_path.is_file():
        raise ConfigError(f"web configuration file does not exist: {config_path}")
    yaml = YAML(typ="safe")
    try:
        document = yaml.load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # YAML parser errors are intentionally operator-only.
        raise ConfigError(f"invalid web configuration YAML: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"webinterface"}:
        raise ConfigError(
            "configuration must contain exactly one top-level 'webinterface' mapping"
        )
    raw = document["webinterface"]
    if not isinstance(raw, dict):
        raise ConfigError("webinterface must be a mapping")
    raw = _resolve_paths(raw, config_path.parent)
    raw = _apply_environment(raw, environ or os.environ)
    try:
        web = WebinterfaceConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"invalid webinterface configuration: {exc}") from exc
    secret = (environ or os.environ).get(web.security.link_secret_env)
    if web.security.require_link_secret and not secret:
        raise ConfigError(f"required secret is missing: {web.security.link_secret_env}")
    upload_key = (environ or os.environ).get(web.security.upload_hmac_env)
    if upload_key is not None and len(upload_key.encode("utf-8")) < 32:
        raise ConfigError("upload HMAC key must contain at least 32 bytes")
    backup_key = (environ or os.environ).get(web.backup.signing_key_env)
    if backup_key is not None and len(backup_key.encode("utf-8")) < 32:
        raise ConfigError("backup signing key must contain at least 32 bytes")
    if web.backup.enabled and backup_key is None:
        raise ConfigError(f"required secret is missing: {web.backup.signing_key_env}")
    kofi_token = (environ or os.environ).get(web.kofi.verification_token_env) or None
    if kofi_token is not None and not 16 <= len(kofi_token.encode("utf-8")) <= 512:
        raise ConfigError("Ko-fi verification token must contain 16 to 512 bytes")
    if web.kofi.enabled and kofi_token is None:
        raise ConfigError(
            f"required secret is missing: {web.kofi.verification_token_env}"
        )
    tara_snapshot = (
        _tara_config_snapshot(web.tara_config_path)
        if web.runner_mode == "tara" and web.tara_config_path is not None
        else None
    )
    return RuntimeConfig(
        web=web,
        link_secret=SecretStr(secret) if secret else None,
        upload_hmac_key=SecretStr(upload_key) if upload_key else None,
        backup_signing_key=SecretStr(backup_key) if backup_key else None,
        kofi_verification_token=SecretStr(kofi_token) if kofi_token else None,
        tara_config_snapshot=tara_snapshot,
    )


def _resolve_paths(raw: dict[str, Any], base: Path) -> dict[str, Any]:
    result = dict(raw)
    storage = dict(result.get("storage", {}))
    for key in ("root", "backups_root", "sqlite_path"):
        if key in storage:
            path = Path(storage[key])
            storage[key] = (path if path.is_absolute() else base / path).resolve(
                strict=False
            )
    result["storage"] = storage
    if result.get("tara_config_path") is not None:
        path = Path(result["tara_config_path"])
        result["tara_config_path"] = (
            path if path.is_absolute() else base / path
        ).absolute()
    return result


def _apply_environment(
    raw: dict[str, Any], environ: Mapping[str, str]
) -> dict[str, Any]:
    """Small allow-list of environment overrides, kept out of versioned YAML."""

    result = dict(raw)
    storage = dict(result.get("storage", {}))
    mapping = {
        "TARA_WEB_STORAGE_ROOT": "root",
        "TARA_WEB_BACKUPS_ROOT": "backups_root",
        "TARA_WEB_SQLITE_PATH": "sqlite_path",
    }
    for variable, key in mapping.items():
        if environ.get(variable):
            path = Path(environ[variable])
            if not path.is_absolute():
                raise ConfigError(f"{variable} must be an absolute path")
            storage[key] = path.resolve(strict=False)
    result["storage"] = storage
    if environ.get("TARA_WEB_PUBLIC_URL"):
        result["public_url"] = environ["TARA_WEB_PUBLIC_URL"]
    if environ.get("TARA_WEB_TARA_CONFIG"):
        path = Path(environ["TARA_WEB_TARA_CONFIG"])
        if not path.is_absolute():
            raise ConfigError("TARA_WEB_TARA_CONFIG must be an absolute path")
        result["tara_config_path"] = path.resolve(strict=False)
    security = dict(result.get("security", {}))
    if environ.get("TARA_WEB_ALLOWED_HOSTS"):
        security["allowed_hosts"] = tuple(environ["TARA_WEB_ALLOWED_HOSTS"].split(","))
    if environ.get("TARA_WEB_ALLOWED_ORIGINS"):
        security["allowed_origins"] = tuple(
            environ["TARA_WEB_ALLOWED_ORIGINS"].split(",")
        )
    result["security"] = security
    return result


def _tara_config_snapshot(path: Path) -> str:
    """Load one bounded regular operator file into a canonical worker snapshot."""
    try:
        info = os.lstat(path)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_TARA_CONFIG_SNAPSHOT_BYTES
            or (
                os.name == "nt"
                and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        ):
            raise ConfigError("Tara configuration file is invalid")
        config = load_tara_config(path)
        snapshot = dumps(
            asdict(config), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
    except ConfigError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ConfigError("Tara configuration file is invalid") from exc
    if len(snapshot.encode("utf-8")) > MAX_TARA_CONFIG_SNAPSHOT_BYTES:
        raise ConfigError("Tara configuration snapshot is too large")
    return snapshot


def public_runtime_config(
    config: RuntimeConfig, languages: tuple[str, ...]
) -> Mapping[str, object]:
    """Return only data explicitly safe for the browser."""

    return MappingProxyType(
        {
            "language": config.web.default_language,
            "locale": config.web.locale,
            "supported_languages": languages,
            "input_modes": ("audio", "merged_transcription", "zip"),
            "max_upload_bytes": config.web.limits.max_upload_bytes,
            "recommended_chunk_bytes": config.web.limits.recommended_chunk_bytes,
            "max_chunk_bytes": config.web.limits.max_chunk_bytes,
            "parallel_uploads": config.web.limits.public_parallel_uploads,
        }
    )
