"""Configuration loading for the standalone Tara application."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from tara.yaml_utils import load_yaml_or_json

if TYPE_CHECKING:
    from tara.analysis.llm_runner import ModelPricing


@dataclass(slots=True)
class LoggingConfig:
    """Logging configuration for local and server runs."""

    level: str = "INFO"
    console_enabled: bool = True
    console_format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create logging configuration from a raw mapping."""
        defaults = cls()
        return cls(
            level=str(data.get("level", defaults.level)),
            console_enabled=bool(data.get("console_enabled", defaults.console_enabled)),
            console_format=str(data.get("console_format", defaults.console_format)),
        )


@dataclass(slots=True)
class TranscriptionConfig:
    """Configuration for the local transcription inference server."""

    model: str = "parakeet:nvidia/parakeet-tdt-0.6b-v3"
    inference_endpoint: str = "http://localhost:8000"
    inference_auth_provider: str = "none"
    modal_proxy_key_env: str = "TARA_MODAL_PROXY_AUTH_KEY"
    modal_proxy_secret_env: str = "TARA_MODAL_PROXY_AUTH_SECRET"
    bearer_token_env: str = "INFERENCE_BEARER_TOKEN"
    request_timeout_seconds: int = 1800
    streaming_enabled: bool = True
    parallelism: int = 1
    modal_progress_interval_seconds: float = 3.0
    modal_max_audio_bytes: int = 1024 * 1024 * 1024
    modal_usd_per_second: str | None = None
    modal_max_containers: int = 3
    modal_files_per_container: int = 2
    modal_queue_grace_seconds: int = 1800
    output_dir: str = "transcriptions"
    recursive: bool = True
    audio_extensions: list[str] = field(
        default_factory=lambda: [
            ".mp3",
            ".wav",
            ".ogg",
            ".flac",
            ".m4a",
            ".aac",
            ".wma",
        ],
    )

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any], *, apply_environment: bool = True
    ) -> Self:
        """Create transcription configuration from a raw mapping."""
        defaults = cls()
        extensions = data.get("audio_extensions", defaults.audio_extensions)
        if not isinstance(extensions, list):
            extensions = defaults.audio_extensions

        def configured(name: str, key: str, default: object) -> object:
            value = data.get(key, default)
            return os.environ.get(name, value) if apply_environment else value

        endpoint = configured(
            "TARA_INFERENCE_ENDPOINT", "inference_endpoint", defaults.inference_endpoint
        )
        auth_provider = configured(
            "TARA_INFERENCE_AUTH_PROVIDER",
            "inference_auth_provider",
            defaults.inference_auth_provider,
        )
        parallelism_value = configured(
            "TARA_TRANSCRIPTION_PARALLELISM", "parallelism", defaults.parallelism
        )
        if str(parallelism_value).strip().lower() == "all":
            parallelism = 0
        else:
            parallelism = max(1, int(parallelism_value))

        progress_interval = configured(
            "TARA_MODAL_PROGRESS_INTERVAL_SECONDS",
            "modal_progress_interval_seconds",
            defaults.modal_progress_interval_seconds,
        )
        max_audio_bytes = configured(
            "TARA_MODAL_MAX_AUDIO_BYTES",
            "modal_max_audio_bytes",
            defaults.modal_max_audio_bytes,
        )
        modal_usd_per_second = configured(
            "TARA_MODAL_USD_PER_SECOND",
            "modal_usd_per_second",
            defaults.modal_usd_per_second,
        )
        if modal_usd_per_second is not None:
            modal_usd_per_second = str(modal_usd_per_second)
        max_containers = configured(
            "TARA_MODAL_MAX_CONTAINERS",
            "modal_max_containers",
            defaults.modal_max_containers,
        )
        files_per_container = configured(
            "TARA_MODAL_FILES_PER_CONTAINER",
            "modal_files_per_container",
            defaults.modal_files_per_container,
        )
        queue_grace_seconds = configured(
            "TARA_MODAL_QUEUE_GRACE_SECONDS",
            "modal_queue_grace_seconds",
            defaults.modal_queue_grace_seconds,
        )

        return cls(
            model=str(data.get("model", defaults.model)),
            inference_endpoint=str(endpoint),
            inference_auth_provider=str(auth_provider),
            modal_proxy_key_env=str(
                data.get("modal_proxy_key_env", defaults.modal_proxy_key_env),
            ),
            modal_proxy_secret_env=str(
                data.get("modal_proxy_secret_env", defaults.modal_proxy_secret_env),
            ),
            bearer_token_env=str(
                data.get("bearer_token_env", defaults.bearer_token_env),
            ),
            request_timeout_seconds=int(
                data.get("request_timeout_seconds", defaults.request_timeout_seconds),
            ),
            streaming_enabled=bool(
                data.get("streaming_enabled", defaults.streaming_enabled),
            ),
            parallelism=parallelism,
            modal_progress_interval_seconds=float(progress_interval),
            modal_max_audio_bytes=int(max_audio_bytes),
            modal_usd_per_second=modal_usd_per_second,
            modal_max_containers=max(1, int(max_containers)),
            modal_files_per_container=max(1, int(files_per_container)),
            modal_queue_grace_seconds=max(60, int(queue_grace_seconds)),
            output_dir=str(data.get("output_dir", defaults.output_dir)),
            recursive=bool(data.get("recursive", defaults.recursive)),
            audio_extensions=[str(extension) for extension in extensions],
        )


@dataclass(slots=True)
class ProcessingConfig:
    """Configuration for merged transcription processing."""

    enabled: bool = True
    output_filename: str = "merged_transcription.yaml"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create processing configuration from a raw mapping."""
        defaults = cls()
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            output_filename=str(data.get("output_filename", defaults.output_filename)),
        )


@dataclass(slots=True)
class ModelPricingConfig:
    """Per-million token pricing for one model."""

    input_usd: float
    output_usd: float
    cached_input_usd: float = 0.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create model pricing from a raw mapping."""
        return cls(
            input_usd=float(data.get("input_usd", 0.0)),
            output_usd=float(data.get("output_usd", 0.0)),
            cached_input_usd=float(data.get("cached_input_usd", 0.0)),
        )


@dataclass(slots=True)
class AnalysisLLMConfig:
    """Configuration for future LLM-assisted analysis calls."""

    backend: str = "deterministic"
    model: str = "Auto"
    default_api_model: str | None = None
    default_pricing_model: str = "composer-2.5"
    pricing_per_million_tokens: dict[str, ModelPricingConfig] = field(
        default_factory=lambda: {
            "composer-2.5": ModelPricingConfig(
                input_usd=0.5,
                cached_input_usd=0.2,
                output_usd=2.5,
            ),
        },
    )
    cursor_command: str = "agent"
    cursor_args: list[str] = field(default_factory=lambda: ["-p"])
    timeout_seconds: int = 900
    retries: int = 2
    cursor_cli_probe: bool = False
    cursor_cli_specialist_tool: bool = False
    usd_to_eur_rate: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create LLM configuration from a raw mapping."""
        defaults = cls()
        cursor_args = data.get("cursor_args", defaults.cursor_args)
        if not isinstance(cursor_args, list):
            cursor_args = defaults.cursor_args
        default_api = data.get("default_api_model", defaults.default_api_model)
        if default_api is not None:
            default_api = str(default_api)
        rate = data.get("usd_to_eur_rate", defaults.usd_to_eur_rate)
        if rate is not None:
            rate = str(rate)
        pricing_raw = data.get(
            "pricing_per_million_tokens",
            defaults.pricing_per_million_tokens,
        )
        pricing: dict[str, ModelPricingConfig] = {}
        if isinstance(pricing_raw, Mapping):
            for model_name, model_pricing in pricing_raw.items():
                if isinstance(model_pricing, Mapping):
                    pricing[str(model_name)] = ModelPricingConfig.from_mapping(
                        model_pricing,
                    )
        if not pricing:
            pricing = dict(defaults.pricing_per_million_tokens)
        return cls(
            backend=str(data.get("backend", defaults.backend)),
            model=str(data.get("model", defaults.model)),
            default_api_model=default_api,
            default_pricing_model=str(
                data.get("default_pricing_model", defaults.default_pricing_model),
            ),
            pricing_per_million_tokens=pricing,
            cursor_command=str(data.get("cursor_command", defaults.cursor_command)),
            cursor_args=[str(arg) for arg in cursor_args],
            timeout_seconds=int(data.get("timeout_seconds", defaults.timeout_seconds)),
            retries=int(data.get("retries", defaults.retries)),
            cursor_cli_probe=bool(
                data.get("cursor_cli_probe", defaults.cursor_cli_probe),
            ),
            cursor_cli_specialist_tool=bool(
                data.get(
                    "cursor_cli_specialist_tool",
                    defaults.cursor_cli_specialist_tool,
                ),
            ),
            usd_to_eur_rate=rate,
        )


def build_pricing_by_model(
    llm_config: AnalysisLLMConfig,
) -> dict[str, ModelPricing]:
    """Build a runner pricing table from analysis LLM configuration.

    Args:
        llm_config: Analysis LLM configuration section.

    Returns:
        Mapping of model names to pricing objects for cost estimation.
    """
    from tara.analysis.llm_runner import ModelPricing

    table: dict[str, ModelPricing] = {}
    for model_name, entry in llm_config.pricing_per_million_tokens.items():
        table[model_name] = ModelPricing(
            input_usd_per_million=entry.input_usd,
            cached_input_usd_per_million=entry.cached_input_usd,
            output_usd_per_million=entry.output_usd,
        )
    default_key = llm_config.default_pricing_model
    default_pricing = table.get(default_key)
    if default_pricing is not None:
        table.setdefault("Auto", default_pricing)
        table.setdefault(default_key, default_pricing)
    return table


@dataclass(slots=True)
class AnalysisScenesConfig:
    """Configuration for scene-driven blackboard enrichment."""

    enabled: bool = True
    boundaries_filename: str = "scene_analysis.yaml"
    descriptions_filename: str = "scene_descriptions.yaml"
    scenes_dir: str = "scenes"
    scene_file_prefix: str = "scene_"
    boundary_block_seconds: float = 1800.0
    resume_partial_descriptions: bool = True
    inject_into_blackboard: bool = True
    fail_on_scene_error: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create scene analysis configuration from a raw mapping."""
        defaults = cls()
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            boundaries_filename=str(
                data.get("boundaries_filename", defaults.boundaries_filename),
            ),
            descriptions_filename=str(
                data.get("descriptions_filename", defaults.descriptions_filename),
            ),
            scenes_dir=str(data.get("scenes_dir", defaults.scenes_dir)),
            scene_file_prefix=str(
                data.get("scene_file_prefix", defaults.scene_file_prefix),
            ),
            boundary_block_seconds=float(
                data.get("boundary_block_seconds", defaults.boundary_block_seconds),
            ),
            resume_partial_descriptions=bool(
                data.get(
                    "resume_partial_descriptions",
                    defaults.resume_partial_descriptions,
                ),
            ),
            inject_into_blackboard=bool(
                data.get("inject_into_blackboard", defaults.inject_into_blackboard),
            ),
            fail_on_scene_error=bool(
                data.get("fail_on_scene_error", defaults.fail_on_scene_error),
            ),
        )


@dataclass(slots=True)
class PromptSecurityConfig:
    """Fail-closed Cursor CLI screening for untrusted text inputs."""

    enabled: bool = False
    minimum_score: int = 80
    max_chars_per_request: int = 60_000
    parallelism: int = 2
    model: str = "Auto"

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        apply_environment: bool = True,
    ) -> Self:
        """Create prompt-security configuration from a raw mapping."""
        defaults = cls()
        minimum_score = int(data.get("minimum_score", defaults.minimum_score))
        max_chars = int(
            data.get("max_chars_per_request", defaults.max_chars_per_request)
        )
        parallelism_raw = data.get("parallelism", defaults.parallelism)
        if apply_environment:
            parallelism_raw = os.environ.get(
                "TARA_PROMPT_SECURITY_PARALLELISM",
                parallelism_raw,
            )
        parallelism = int(parallelism_raw)
        if not 0 <= minimum_score <= 100:
            raise ValueError("analysis.prompt_security.minimum_score must be 0..100")
        if max_chars < 4_000:
            raise ValueError(
                "analysis.prompt_security.max_chars_per_request must be at least 4000"
            )
        if not 1 <= parallelism <= 8:
            raise ValueError(
                "analysis.prompt_security.parallelism must be between 1 and 8"
            )
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            minimum_score=minimum_score,
            max_chars_per_request=max_chars,
            parallelism=parallelism,
            model=str(data.get("model", defaults.model)),
        )


@dataclass(slots=True)
class AnalysisConfig:
    """Configuration for blackboard analysis output and loop behavior."""

    enabled: bool = True
    pipeline: str = "blackboard_v1"
    context_path: str | None = None
    prior_context_path: str | None = None
    output_dir: str = "analysis"
    summary_markdown_filename: str = "session_summary.md"
    summary_json_filename: str = "session_summary.yaml"
    max_audit_attempts: int = 3
    target_window_seconds: float = 90.0
    overlap_seconds: float = 20.0
    parallel: bool = True
    parallelism: int = 4
    scenes: AnalysisScenesConfig = field(default_factory=AnalysisScenesConfig)
    prompt_security: PromptSecurityConfig = field(default_factory=PromptSecurityConfig)
    llm: AnalysisLLMConfig = field(default_factory=AnalysisLLMConfig)

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any], *, apply_environment: bool = True
    ) -> Self:
        """Create analysis configuration from a raw mapping."""
        defaults = cls()
        llm_data = data.get("llm", {})
        if not isinstance(llm_data, Mapping):
            llm_data = {}
        scenes_data = data.get("scenes", {})
        if not isinstance(scenes_data, Mapping):
            scenes_data = {}
        prompt_security_data = data.get("prompt_security", {})
        if not isinstance(prompt_security_data, Mapping):
            prompt_security_data = {}
        parallel_raw = data.get("parallel", defaults.parallel)
        parallelism_raw = data.get("parallelism", defaults.parallelism)
        if apply_environment:
            parallel_raw = os.environ.get("TARA_ANALYSIS_PARALLEL", parallel_raw)
            parallelism_raw = os.environ.get(
                "TARA_ANALYSIS_PARALLELISM",
                parallelism_raw,
            )
        parallel = str(parallel_raw).strip().lower() in {"1", "true", "yes", "on"}
        parallelism = int(parallelism_raw)
        if not 1 <= parallelism <= 16:
            raise ValueError("analysis.parallelism must be between 1 and 16")
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            pipeline=str(data.get("pipeline", defaults.pipeline)),
            context_path=_optional_str(data.get("context_path")),
            prior_context_path=_optional_str(data.get("prior_context_path")),
            output_dir=str(data.get("output_dir", defaults.output_dir)),
            summary_markdown_filename=str(
                data.get(
                    "summary_markdown_filename",
                    defaults.summary_markdown_filename,
                ),
            ),
            summary_json_filename=str(
                data.get("summary_json_filename", defaults.summary_json_filename),
            ),
            max_audit_attempts=int(
                data.get("max_audit_attempts", defaults.max_audit_attempts),
            ),
            target_window_seconds=float(
                data.get("target_window_seconds", defaults.target_window_seconds),
            ),
            overlap_seconds=float(
                data.get("overlap_seconds", defaults.overlap_seconds),
            ),
            parallel=parallel,
            parallelism=parallelism,
            scenes=AnalysisScenesConfig.from_mapping(scenes_data),
            prompt_security=PromptSecurityConfig.from_mapping(
                prompt_security_data,
                apply_environment=apply_environment,
            ),
            llm=AnalysisLLMConfig.from_mapping(llm_data),
        )


@dataclass(slots=True)
class TaraConfig:
    """Root configuration object for the standalone Tara application."""

    language: str = "fr"
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any], *, apply_environment: bool = True
    ) -> Self:
        """Create a root configuration object from a raw mapping."""
        return cls(
            language=str(data.get("language", "fr")),
            logging=LoggingConfig.from_mapping(_mapping(data.get("logging"))),
            transcription=TranscriptionConfig.from_mapping(
                _mapping(data.get("transcription")),
                apply_environment=apply_environment,
            ),
            processing=ProcessingConfig.from_mapping(_mapping(data.get("processing"))),
            analysis=AnalysisConfig.from_mapping(
                _mapping(data.get("analysis")),
                apply_environment=apply_environment,
            ),
        )


def load_dotenv(path: Path | None = None) -> None:
    """Load simple `KEY=VALUE` entries from a `.env` file into the environment.

    Existing environment variables are preserved.
    """
    dotenv_path = path or _project_root() / ".env"
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def find_default_config_path() -> Path | None:
    """Return the bundled default configuration path when it exists."""
    config_dir = _project_root() / "config"
    yaml_path = config_dir / "configuration.yaml"
    if yaml_path.exists():
        return yaml_path
    json_path = config_dir / "configuration.json"
    return json_path if json_path.exists() else None


def load_config(config_path: Path | None = None) -> TaraConfig:
    """Load Tara configuration from YAML or legacy JSON, falling back to defaults.

    Args:
        config_path: Optional explicit configuration path.

    Returns:
        Parsed configuration object.
    """
    load_dotenv()
    path = config_path or find_default_config_path()
    if path is None:
        return TaraConfig()
    raw = load_yaml_or_json(path)
    if not isinstance(raw, Mapping):
        raise ValueError("Configuration root must be a mapping object.")
    return TaraConfig.from_mapping(raw)


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping value or an empty mapping for invalid sections."""
    return value if isinstance(value, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    """Return a stripped string or `None`."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _project_root() -> Path:
    """Return the source-tree project root for bundled config and `.env`."""
    return Path(__file__).resolve().parents[2]
