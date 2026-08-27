"""Common LLM runner abstraction for Tara analysis agents.

This module keeps concrete LLM execution details behind a small interface so
analysis agents can request completions without knowing whether the call is sent
to an API backend or to Cursor CLI.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import requests

from tara.providers.costing import CostSnapshot, convert_native_cost
from tara.providers.events import UsageAttempt

_LOGGER = logging.getLogger(__name__)

LLMBackendName = Literal["api", "cursor_cli"]
CursorPromptTransport = Literal["stdin", "argv"]
RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_CURSOR_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "COMSPEC",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    # Cursor CLI reads credentials and config from standard Windows profile paths.
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "USERNAME",
    "USERDOMAIN",
    # Linux Cursor CLI resolves file-backed authentication below HOME/XDG.
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "AGENT_CLI_CREDENTIAL_STORE",
)
CURSOR_ENV_DENYLIST_EXACT = {
    "TARA_MODAL_PROXY_AUTH_KEY",
    "TARA_MODAL_PROXY_AUTH_SECRET",
    "TARA_INFERENCE_ENDPOINT",
    "TARA_INFERENCE_AUTH_PROVIDER",
}
CURSOR_ENV_DENYLIST_SUBSTRINGS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "KEY",
)
CURSOR_ENV_DENYLIST_EXCEPTIONS = {
    # Required Windows system variables despite containing "KEY".
    "PATHEXT",
}


class LLMRunnerError(RuntimeError):
    """Base exception raised by LLM runner failures."""


class LLMConfigurationError(LLMRunnerError):
    """Raised when the LLM runner configuration is invalid."""


class LLMBackendError(LLMRunnerError):
    """Raised when a backend fails to produce a completion."""


@dataclass(slots=True)
class ModelPricing:
    """Token pricing for cost estimation.

    Attributes:
        input_usd_per_million: Cost for one million non-cached input tokens.
        output_usd_per_million: Cost for one million output tokens.
        cached_input_usd_per_million: Cost for one million cached input tokens.
    """

    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float = 0.0

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float:
        """Estimate a request cost in USD.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cached_input_tokens: Number of cached input tokens, if available.

        Returns:
            Estimated cost in USD.
        """
        cached = min(max(cached_input_tokens, 0), max(input_tokens, 0))
        uncached = max(input_tokens - cached, 0)
        return (
            uncached / 1_000_000.0 * self.input_usd_per_million
            + cached / 1_000_000.0 * self.cached_input_usd_per_million
            + max(output_tokens, 0) / 1_000_000.0 * self.output_usd_per_million
        )

    def estimate_cursor_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> float:
        """Estimate a Cursor CLI request cost in USD.

        Cursor reports input and cache-read counters separately rather than as
        a cached subset of input tokens.

        Args:
            input_tokens: Non-cached input tokens billed at the input rate.
            output_tokens: Output tokens billed at the output rate.
            cache_read_tokens: Cache-read tokens billed at the cache-read rate.

        Returns:
            Estimated cost in USD.
        """
        return (
            max(input_tokens, 0) / 1_000_000.0 * self.input_usd_per_million
            + max(cache_read_tokens, 0)
            / 1_000_000.0
            * self.cached_input_usd_per_million
            + max(output_tokens, 0) / 1_000_000.0 * self.output_usd_per_million
        )


DEFAULT_CURSOR_PRICING_MODEL = "composer-2.5"
DEFAULT_COMPOSER_25_PRICING = ModelPricing(
    input_usd_per_million=0.5,
    cached_input_usd_per_million=0.2,
    output_usd_per_million=2.5,
)


@dataclass(slots=True)
class LLMRequest:
    """Structured request sent by analysis agents.

    Attributes:
        purpose: Business purpose used for telemetry and cost reports.
        system_prompt: System-level instruction.
        user_prompt: User/task prompt.
        response_format: Optional structured response format descriptor.
        model: Optional model override. API requests require either this value or
            a configured model.
        temperature: Sampling temperature.
        max_output_tokens: Optional output token budget.
        metadata: Additional agent/run metadata.
    """

    purpose: str
    system_prompt: str
    user_prompt: str
    response_format: Mapping[str, Any] | None = None
    model: str | None = None
    stage: str | None = None
    temperature: float = 0.0
    max_output_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    """Structured response returned by all LLM backends.

    Attributes:
        content: Completion text.
        model: Model used by the backend.
        backend: Backend name.
        input_tokens: Input token count if available.
        output_tokens: Output token count if available.
        total_tokens: Total token count if available.
        raw_usage: Raw backend usage payload.
        estimated_cost_usd: Estimated request cost, if pricing is configured.
        metadata: Backend-specific metadata safe for logs.
    """

    content: str
    model: str
    backend: LLMBackendName
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    raw_usage: Mapping[str, Any] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMRunnerConfig:
    """Configuration for the shared LLM runner.

    Attributes:
        backend: Selected backend, `api` or `cursor_cli`.
        model: Default model. API backend requires an explicit non-empty model.
        api_base_url: Base URL for OpenAI-compatible Responses API calls.
        api_key_env: Environment variable that contains the API key.
        timeout_seconds: Backend timeout in seconds.
        max_retries: Retry count after the first attempt for transient failures.
        retry_backoff_seconds: Initial exponential backoff duration.
        cursor_command: Cursor CLI command.
        cursor_args: Cursor CLI arguments preceding the prompt.
        cursor_prompt_transport: How to pass the prompt to Cursor CLI. `stdin`
            avoids exposing prompt text in process arguments and is the default.
        cursor_env_allowlist: Environment variables inherited by Cursor CLI.
        include_cursor_stderr: Include successful Cursor CLI stderr in response
            metadata. Disabled by default to avoid leaking diagnostics.
        telemetry_metadata_keys: Request metadata keys that are safe to forward
            to telemetry. All metadata is dropped by default.
        pricing_by_model: Optional pricing table for cost estimation.
    """

    backend: LLMBackendName = "api"
    model: str | None = None
    api_base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: int = 900
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    cursor_command: str = "agent"
    cursor_args: Sequence[str] = field(default_factory=lambda: ("-p",))
    cursor_prompt_transport: CursorPromptTransport = "stdin"
    cursor_env_allowlist: Sequence[str] = field(
        default_factory=lambda: DEFAULT_CURSOR_ENV_ALLOWLIST
    )
    include_cursor_stderr: bool = False
    telemetry_metadata_keys: Sequence[str] = field(default_factory=tuple)
    pricing_by_model: Mapping[str, ModelPricing] = field(default_factory=dict)
    usd_to_eur_rate: str | None = None


class TelemetryRecorder(Protocol):
    """Protocol for telemetry services used by `LLMRunner`."""

    def record(self, name: str, payload: Mapping[str, Any]) -> None:
        """Record a telemetry event.

        Args:
            name: Event name.
            payload: JSON-serializable event payload.
        """


class LLMBackend(Protocol):
    """Protocol implemented by concrete LLM backends."""

    backend_name: LLMBackendName

    def run(self, request: LLMRequest) -> LLMResponse:
        """Execute a request and return a normalized response."""


class OpenAIAPIBackend:
    """OpenAI-compatible Responses API backend."""

    backend_name: LLMBackendName = "api"

    def __init__(
        self,
        config: LLMRunnerConfig,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize the API backend.

        Args:
            config: Runner configuration.
            http_post: Optional callable used for tests instead of
                `requests.post`.

        Raises:
            LLMConfigurationError: If the API model is not configured.
        """
        if not config.model:
            raise LLMConfigurationError("API backend requires an explicit model.")
        self._config = config
        self._http_post = http_post or requests.post

    def run(self, request: LLMRequest) -> LLMResponse:
        """Execute a request through an OpenAI-compatible Responses API."""
        model = request.model or self._config.model
        if not model:
            raise LLMConfigurationError("API request requires an explicit model.")

        api_key = os.getenv(self._config.api_key_env)
        if not api_key:
            raise LLMConfigurationError(
                f"Missing API key environment variable: {self._config.api_key_env}"
            )

        payload: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)

        try:
            response = self._http_post(
                f"{self._config.api_base_url.rstrip('/')}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._config.timeout_seconds,
            )
            self._raise_for_status(response)
            data = self._to_mapping(response.json())
        except LLMRunnerError:
            raise
        except requests.RequestException as exc:
            raise LLMBackendError(f"API transport failed: {exc}") from exc
        except ValueError as exc:
            raise LLMBackendError("API response JSON could not be decoded.") from exc
        content = self._extract_content(data)
        usage = self._to_mapping(data.get("usage", {}))
        input_tokens = self._to_int(
            usage.get("input_tokens", usage.get("prompt_tokens", 0))
        )
        output_tokens = self._to_int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        )
        total_tokens = self._to_int(
            usage.get("total_tokens", input_tokens + output_tokens)
        )
        cached_tokens = self._to_int(
            self._to_mapping(usage.get("input_tokens_details", {})).get(
                "cached_tokens",
                usage.get("cached_input_tokens", 0),
            )
        )

        pricing = self._config.pricing_by_model.get(model)
        estimated_cost = (
            pricing.estimate_cost(input_tokens, output_tokens, cached_tokens)
            if pricing
            else None
        )
        return LLMResponse(
            content=content,
            model=str(data.get("model", model)),
            backend=self.backend_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            raw_usage=usage,
            estimated_cost_usd=estimated_cost,
        )

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        """Raise a backend error if the HTTP response failed."""
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code and status_code not in RETRYABLE_HTTP_STATUS_CODES:
                raise LLMConfigurationError(
                    f"API request failed with non-retryable status {status_code}."
                ) from exc
            raise LLMBackendError(f"API request failed: {exc}") from exc
        except requests.RequestException as exc:
            raise LLMBackendError(f"API request failed: {exc}") from exc

    @classmethod
    def _extract_content(cls, data: Mapping[str, Any]) -> str:
        """Extract text from common OpenAI Responses API payload shapes."""
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

        parts: list[str] = []
        output = data.get("output", [])
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, Mapping):
                        continue
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        if parts:
            return "\n".join(parts)

        raise LLMBackendError("API response did not contain completion text.")

    @staticmethod
    def _to_mapping(value: Any) -> Mapping[str, Any]:
        """Return a mapping value or an empty mapping."""
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _to_int(value: Any) -> int:
        """Convert backend token values to non-negative integers."""
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0


def _resolve_cursor_cli_executable(command: str) -> str:
    """Return an executable path for the Cursor ``agent`` CLI.

    On Windows, ``CreateProcess`` often fails for the bare name ``agent`` even
    when ``PATH`` contains ``cursor-agent``; ``shutil.which`` or the default
    install location yields ``agent.cmd``.

    Args:
        command: Configured ``cursor_command`` (typically ``agent``).

    Returns:
        Path or name to pass as the subprocess argv0.
    """
    if command.strip().lower() not in {"agent", "agent.cmd", "cursor-agent"}:
        return command
    found = shutil.which(command)
    if found:
        return found
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidate = os.path.join(local_app, "cursor-agent", "agent.cmd")
            if os.path.isfile(candidate):
                return candidate
    return command


class CursorCLIBackend:
    """Cursor CLI backend using `agent -p` in non-interactive mode."""

    backend_name: LLMBackendName = "cursor_cli"

    def __init__(
        self,
        config: LLMRunnerConfig,
        subprocess_run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Initialize the Cursor CLI backend.

        Args:
            config: Runner configuration.
            subprocess_run: Optional subprocess runner for tests.
        """
        self._config = config
        self._subprocess_run = subprocess_run or subprocess.run

    def run(self, request: LLMRequest) -> LLMResponse:
        """Execute a request with Cursor CLI and normalize stdout.

        Args:
            request: Structured LLM request.

        Returns:
            Normalized Cursor CLI response.

        Raises:
            LLMBackendError: If Cursor CLI is missing, times out, exits with an
                error, or returns empty output.
        """
        prompt = self._build_prompt(request)
        executable = _resolve_cursor_cli_executable(self._config.cursor_command)
        command = [
            executable,
            *_cursor_args_with_json_output(self._config.cursor_args),
        ]
        input_text = prompt
        if self._config.cursor_prompt_transport == "argv":
            command.append(prompt)
            input_text = None
        run_kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": self._config.timeout_seconds,
            "check": False,
            "input": input_text,
            "env": self._cursor_environment(),
            "cwd": str(self._cursor_working_directory()),
        }
        try:
            # Force UTF-8 decoding for Cursor CLI output to avoid Windows locale
            # mojibake in French summaries.
            result = self._subprocess_run(
                command,
                encoding="utf-8",
                errors="replace",
                **run_kwargs,
            )
        except TypeError:
            # Test doubles may not accept encoding/errors kwargs.
            try:
                result = self._subprocess_run(command, **run_kwargs)
            except FileNotFoundError as exc:
                raise LLMBackendError(
                    f"Cursor CLI command not found: {self._config.cursor_command}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise LLMBackendError("Cursor CLI request timed out.") from exc
        except FileNotFoundError as exc:
            raise LLMBackendError(
                f"Cursor CLI command not found: {self._config.cursor_command}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMBackendError("Cursor CLI request timed out.") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LLMBackendError(
                f"Cursor CLI failed with exit code {result.returncode}: {stderr}"
            )

        stdout_text = _repair_windows_utf8_mojibake(result.stdout or "").strip()
        if not stdout_text:
            raise LLMBackendError("Cursor CLI returned empty output.")

        try:
            content, usage = _parse_cursor_cli_stdout(stdout_text)
        except ValueError as exc:
            stderr = (result.stderr or "").strip()
            excerpt = stderr[:200] if stderr else "none"
            raise LLMBackendError(
                f"Cursor CLI returned non-JSON output: {exc}. stderr: {excerpt}"
            ) from exc

        content = _repair_windows_utf8_mojibake(content)

        if not content:
            raise LLMBackendError(
                "Cursor CLI returned empty completion in JSON result."
            )

        model_name = request.model or self._config.model or "Auto"
        pricing = _resolve_cursor_pricing(self._config.pricing_by_model, model_name)
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        raw_usage: dict[str, Any] = {}
        if usage is not None:
            input_tokens = usage["input_tokens"]
            output_tokens = usage["output_tokens"]
            cache_read_tokens = usage["cache_read_tokens"]
            raw_usage = dict(usage)

        total_tokens = input_tokens + output_tokens + cache_read_tokens
        estimated_cost = (
            pricing.estimate_cursor_cost(
                input_tokens,
                output_tokens,
                cache_read_tokens,
            )
            if pricing is not None
            else None
        )

        metadata: dict[str, Any] = {"token_source": "cursor_cli_json"}
        if self._config.include_cursor_stderr:
            metadata["stderr"] = (result.stderr or "").strip()

        return LLMResponse(
            content=content,
            model=model_name,
            backend=self.backend_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            raw_usage=raw_usage,
            estimated_cost_usd=estimated_cost,
            metadata=metadata,
        )

    @staticmethod
    def _build_prompt(request: LLMRequest) -> str:
        """Build the JSON prompt payload for Cursor CLI.

        Args:
            request: Structured LLM request.

        Returns:
            JSON payload containing purpose, prompts, response format, and
            metadata. The payload is sent via stdin by default to avoid process
            argument exposure.
        """
        payload = {
            "purpose": request.purpose,
            "system_prompt": request.system_prompt,
            "user_prompt": request.user_prompt,
            "response_format": request.response_format,
            "metadata": dict(request.metadata),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _cursor_environment(self) -> dict[str, str]:
        """Build a sanitized environment for Cursor CLI.

        Returns:
            A dictionary containing only allow-listed environment variables.
        """
        allowlist = {item.upper() for item in self._config.cursor_env_allowlist}
        return {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowlist and not _is_denied_cursor_env_key(key)
        }

    @staticmethod
    def _cursor_working_directory() -> str:
        """Return a secret-free working directory for Cursor CLI subprocesses."""
        sandbox_override = os.environ.get("TARA_CURSOR_CLI_SANDBOX_DIR")
        candidates: list[str] = []
        if sandbox_override:
            candidates.append(sandbox_override)
        candidates.append(
            r"C:\tmp\tara_cursor_cli_sandbox"
            if os.name == "nt"
            else "/tmp/tara_cursor_cli_sandbox"
        )
        candidates.append(
            os.path.join(tempfile.gettempdir(), "tara_cursor_cli_sandbox")
        )

        last_error: OSError | None = None
        for sandbox in dict.fromkeys(candidates):
            try:
                os.makedirs(sandbox, exist_ok=True)
            except OSError as exc:
                last_error = exc
                continue
            return sandbox

        raise LLMBackendError(
            "Unable to create a Cursor CLI sandbox directory. Set "
            "TARA_CURSOR_CLI_SANDBOX_DIR to a writable directory outside TaraRepo."
        ) from last_error


def _is_denied_cursor_env_key(key: str) -> bool:
    """Return whether an environment variable must be hidden from Cursor CLI."""
    normalized = key.upper()
    if normalized in CURSOR_ENV_DENYLIST_EXCEPTIONS:
        return False
    if normalized in CURSOR_ENV_DENYLIST_EXACT:
        return True
    return any(marker in normalized for marker in CURSOR_ENV_DENYLIST_SUBSTRINGS)


def _cursor_args_with_sandbox_trust(args: Sequence[str]) -> list[str]:
    """Return Cursor args constrained to read-only ask mode and its sandbox."""
    result = [str(arg) for arg in args]
    normalized = {arg.strip().lower() for arg in result}
    forbidden = {"--yolo", "-f", "--force", "--auto-review", "--approve-mcps"}
    if (
        normalized & forbidden
        or any(
            any(arg.startswith(f"{flag}=") for flag in forbidden) for arg in normalized
        )
        or "--sandbox=disabled" in normalized
    ):
        raise LLMConfigurationError(
            "Cursor CLI execution-enabling flags are forbidden for untrusted input."
        )
    for index, arg in enumerate(result):
        lowered = arg.strip().lower()
        if lowered == "--mode" and (
            index + 1 >= len(result) or result[index + 1].strip().lower() != "ask"
        ):
            raise LLMConfigurationError(
                "Cursor CLI must use read-only ask mode for untrusted input."
            )
        if lowered.startswith("--mode=") and lowered != "--mode=ask":
            raise LLMConfigurationError(
                "Cursor CLI must use read-only ask mode for untrusted input."
            )
        if lowered == "--sandbox" and (
            index + 1 >= len(result) or result[index + 1].strip().lower() != "enabled"
        ):
            raise LLMConfigurationError(
                "Cursor CLI sandbox must be enabled for untrusted input."
            )
    if normalized.isdisjoint({"--trust", "--yolo", "-f"}):
        result.insert(0, "--trust")
    if not any(
        arg.strip().lower() == "--mode" or arg.strip().lower().startswith("--mode=")
        for arg in result
    ):
        result[0:0] = ["--mode", "ask"]
    if not any(
        arg.strip().lower() == "--sandbox"
        or arg.strip().lower().startswith("--sandbox=")
        for arg in result
    ):
        result[0:0] = ["--sandbox", "enabled"]
    return result


def _cursor_args_with_json_output(args: Sequence[str]) -> list[str]:
    """Return Cursor args with ``--output-format json`` for usage reporting."""
    result = _cursor_args_with_sandbox_trust(args)
    normalized_lower = [arg.strip().lower() for arg in result]
    if "--output-format" in normalized_lower:
        return result
    insert_at = len(result)
    for index, arg in enumerate(result):
        if arg.strip().lower() == "-p":
            insert_at = index + 1
            break
    result[insert_at:insert_at] = ["--output-format", "json"]
    return result


def _parse_cursor_cli_stdout(stdout: str) -> tuple[str, dict[str, int] | None]:
    """Parse a Cursor CLI JSON result payload from stdout.

    Args:
        stdout: Raw stdout emitted by ``cursor-agent --output-format json``.

    Returns:
        Tuple of completion text and normalized usage counters when present.

    Raises:
        ValueError: If stdout does not contain a successful result event.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    candidates = list(reversed(lines)) if lines else [stdout.strip()]
    last_error: ValueError | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = ValueError(f"invalid JSON: {exc}")
            continue
        if not isinstance(data, dict):
            last_error = ValueError("expected JSON object")
            continue
        if data.get("type") != "result":
            last_error = ValueError("missing result event")
            continue
        if data.get("subtype") != "success":
            last_error = ValueError(f"non-success result: {data.get('subtype')!r}")
            continue
        result_text = data.get("result", "")
        if not isinstance(result_text, str):
            last_error = ValueError("result field is not a string")
            continue
        usage_raw = data.get("usage")
        usage: dict[str, int] | None = None
        if isinstance(usage_raw, Mapping):
            usage = {
                "input_tokens": _to_int(
                    usage_raw.get("inputTokens", usage_raw.get("input_tokens", 0)),
                ),
                "output_tokens": _to_int(
                    usage_raw.get("outputTokens", usage_raw.get("output_tokens", 0)),
                ),
                "cache_read_tokens": _to_int(
                    usage_raw.get(
                        "cacheReadTokens",
                        usage_raw.get("cache_read_tokens", 0),
                    ),
                ),
                "cache_write_tokens": _to_int(
                    usage_raw.get(
                        "cacheWriteTokens",
                        usage_raw.get("cache_write_tokens", 0),
                    ),
                ),
            }
        return result_text.strip(), usage
    raise last_error or ValueError("empty stdout")


def _resolve_cursor_pricing(
    pricing_by_model: Mapping[str, ModelPricing],
    model_name: str,
) -> ModelPricing | None:
    """Resolve pricing for a Cursor CLI model name.

    Args:
        pricing_by_model: Configured pricing table.
        model_name: Requested or configured model name.

    Returns:
        Matching pricing entry, or the built-in Composer 2.5 default.
    """
    if model_name in pricing_by_model:
        return pricing_by_model[model_name]
    if "Auto" in pricing_by_model:
        return pricing_by_model["Auto"]
    if DEFAULT_CURSOR_PRICING_MODEL in pricing_by_model:
        return pricing_by_model[DEFAULT_CURSOR_PRICING_MODEL]
    return DEFAULT_COMPOSER_25_PRICING


def _to_int(value: Any) -> int:
    """Convert backend token values to non-negative integers."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _repair_windows_utf8_mojibake(text: str) -> str:
    """Repair UTF-8 text that a Windows wrapper decoded as CP-1252."""
    if _mojibake_score(text) == 0:
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text
    if _mojibake_score(repaired) < _mojibake_score(text):
        return repaired
    return text


def _mojibake_score(text: str) -> int:
    """Return a rough score for common French UTF-8/CP-1252 mojibake."""
    markers = ("Ã", "Â", "â€™", "â€œ", "â€", "â€¦", "ðŸ")
    return sum(text.count(marker) for marker in markers)


class LLMRunner:
    """Stable facade used by Tara analysis agents."""

    def __init__(
        self,
        config: LLMRunnerConfig,
        telemetry: TelemetryRecorder | None = None,
        api_backend: LLMBackend | None = None,
        cursor_backend: LLMBackend | None = None,
        sleep: Callable[[float], None] | None = None,
        usage_report: Any | None = None,
        cancellation_check: Callable[[], None] | None = None,
        retry_callback: Callable[[int], None] | None = None,
        usage_attempt_callback: Callable[[UsageAttempt], None] | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            config: Runner configuration.
            telemetry: Optional telemetry recorder.
            api_backend: Optional API backend override for tests.
            cursor_backend: Optional Cursor CLI backend override for tests.
            sleep: Optional sleep callable for retry tests.
            usage_report: Optional :class:`UsageReport` accumulator.
        """
        self._config = config
        self._telemetry = telemetry
        self._sleep = sleep or time.sleep
        self._usage_report = usage_report
        self._cancellation_check = cancellation_check
        self._retry_callback = retry_callback
        self._usage_attempt_callback = usage_attempt_callback
        self._backends: dict[LLMBackendName, LLMBackend] = {}
        if api_backend is not None:
            self._backends["api"] = api_backend
        if cursor_backend is not None:
            self._backends["cursor_cli"] = cursor_backend

    def run(self, prompt: LLMRequest) -> LLMResponse:
        """Execute an LLM request through the configured backend.

        Args:
            prompt: Structured LLM request.

        Returns:
            Normalized LLM response.

        Raises:
            LLMBackendError: If the backend fails after configured retries.
            LLMConfigurationError: If the backend reports a non-retryable
                configuration or client error.
        """
        backend = self._get_backend(self._config.backend)
        attempts = 1 + max(0, self._config.max_retries)
        last_error: LLMBackendError | None = None
        for attempt in range(1, attempts + 1):
            if self._cancellation_check is not None:
                self._cancellation_check()
            started_at = datetime.now(UTC)
            start = time.perf_counter()
            response: LLMResponse | None = None
            status = "failed"
            try:
                response = backend.run(prompt)
                if self._cancellation_check is not None:
                    try:
                        self._cancellation_check()
                    except BaseException:
                        status = "cancelled"
                        raise
                status = "success"
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                prompt_chars = len(prompt.system_prompt) + len(prompt.user_prompt)
                _LOGGER.info(
                    "llm_runner completed purpose=%s backend=%s model=%s "
                    "prompt_chars=%s attempt=%s duration_ms=%s total_tokens=%s",
                    prompt.purpose,
                    response.backend,
                    response.model,
                    prompt_chars,
                    attempt,
                    elapsed_ms,
                    response.total_tokens,
                )
                self._record_telemetry(prompt, response, attempt, elapsed_ms)
                return response
            except TimeoutError:
                status = "timed_out"
                raise
            except LLMBackendError as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                if self._retry_callback is not None:
                    self._retry_callback(attempt + 1)
                if self._cancellation_check is not None:
                    self._cancellation_check()
                self._sleep(self._config.retry_backoff_seconds * (2 ** (attempt - 1)))
                if self._cancellation_check is not None:
                    self._cancellation_check()
            except Exception:
                if status != "cancelled":
                    status = "failed"
                raise
            finally:
                if self._usage_attempt_callback is not None:
                    finished_at = datetime.now(UTC)
                    cost = _llm_cost_snapshot(response, self._config.usd_to_eur_rate)
                    usage_attempt = UsageAttempt(
                        attempt_id="pa_" + secrets.token_urlsafe(18),
                        operation_family="llm",
                        provider=self._config.backend,
                        model=(response.model if response else self._config.model),
                        status=status,
                        started_at=started_at.isoformat().replace("+00:00", "Z"),
                        finished_at=finished_at.isoformat().replace("+00:00", "Z"),
                        input_tokens=response.input_tokens if response else 0,
                        output_tokens=response.output_tokens if response else 0,
                        cache_tokens=_cache_tokens(response),
                        duration_ms=max(0, int((time.perf_counter() - start) * 1000)),
                        cost_micro_eur=cost.cost_micro_eur if cost else None,
                        cost_source=cost.source if cost else "unavailable",
                        native_cost_micros=(cost.native_cost_micros if cost else None),
                        native_currency=cost.native_currency if cost else None,
                        conversion_rate=cost.conversion_rate if cost else None,
                    )
                    try:
                        self._usage_attempt_callback(usage_attempt)
                    except Exception:
                        if status == "success":
                            raise
                        _LOGGER.exception(
                            "provider usage callback failed for a non-success attempt"
                        )

        raise LLMBackendError(
            f"LLM backend {self._config.backend!r} failed after {attempts} attempts."
        ) from last_error

    def _get_backend(self, name: LLMBackendName) -> LLMBackend:
        """Create or return the selected backend."""
        existing = self._backends.get(name)
        if existing is not None:
            return existing
        if name == "api":
            backend = OpenAIAPIBackend(self._config)
        elif name == "cursor_cli":
            backend = CursorCLIBackend(self._config)
        else:
            raise LLMConfigurationError(f"Unsupported LLM backend: {name}")
        self._backends[name] = backend
        return backend

    def _record_telemetry(
        self,
        request: LLMRequest,
        response: LLMResponse,
        attempt: int,
        elapsed_ms: int,
    ) -> None:
        """Record usage telemetry when a recorder is configured."""
        if self._telemetry is not None:
            self._telemetry.record(
                "llm_runner.usage.recorded",
                {
                    "purpose": request.purpose,
                    "stage": request.stage,
                    "backend": response.backend,
                    "model": response.model,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "total_tokens": response.total_tokens,
                    "estimated_cost_usd": response.estimated_cost_usd,
                    "attempt": attempt,
                    "metadata": self._safe_metadata(request.metadata),
                },
            )
        if self._usage_report is not None:
            cache_read_tokens = 0
            if isinstance(response.raw_usage, Mapping):
                cache_read_tokens = int(
                    response.raw_usage.get("cache_read_tokens", 0) or 0,
                )
            self._usage_report.record_call(
                stage=request.stage,
                purpose=request.purpose,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cache_read_tokens=cache_read_tokens,
                total_tokens=response.total_tokens,
                cost_usd=response.estimated_cost_usd,
                latency_ms=elapsed_ms,
                attempt=attempt,
            )

    def _safe_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Return telemetry metadata restricted to configured safe keys.

        Args:
            metadata: Request metadata that may contain private or transcript
                derived values.

        Returns:
            Metadata containing only configured safe keys and scalar values.
        """
        allowed = set(self._config.telemetry_metadata_keys)
        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            if key not in allowed:
                continue
            if isinstance(value, str | int | float | bool) or value is None:
                safe[key] = value
        return safe


def _cache_tokens(response: LLMResponse | None) -> int:
    if response is None or not isinstance(response.raw_usage, Mapping):
        return 0
    value = response.raw_usage.get("cache_read_tokens", 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def _llm_cost_snapshot(
    response: LLMResponse | None, usd_to_eur_rate: str | None
) -> CostSnapshot | None:
    if (
        response is None
        or response.estimated_cost_usd is None
        or usd_to_eur_rate is None
    ):
        return None
    return convert_native_cost(
        response.estimated_cost_usd,
        native_currency="USD",
        eur_per_native=usd_to_eur_rate,
        source="configured_estimate",
    )
