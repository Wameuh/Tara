"""Tests for the shared LLM runner."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

from tara.analysis.llm_runner import (
    CursorCLIBackend,
    LLMBackendError,
    LLMConfigurationError,
    LLMRequest,
    LLMResponse,
    LLMRunner,
    LLMRunnerConfig,
    ModelPricing,
    OpenAIAPIBackend,
)


@dataclass(slots=True)
class FakeHTTPResponse:
    """Minimal fake HTTP response used by API backend tests."""

    payload: Mapping[str, Any]
    status_code: int = 200

    def raise_for_status(self) -> None:
        """Simulate a successful HTTP response."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Mapping[str, Any]:
        """Return the configured JSON payload."""
        return self.payload


class FakeTelemetry:
    """Collect telemetry events emitted by the runner."""

    def __init__(self) -> None:
        """Initialize an empty telemetry event list."""
        self.events: list[tuple[str, Mapping[str, Any]]] = []

    def record(self, name: str, payload: Mapping[str, Any]) -> None:
        """Record an event in memory."""
        self.events.append((name, payload))


class FlakyBackend:
    """Backend that fails once before returning a response."""

    backend_name = "api"

    def __init__(self) -> None:
        """Initialize the failing attempt counter."""
        self.calls = 0

    def run(self, request: LLMRequest) -> LLMResponse:
        """Fail on the first call, then return a successful response."""
        self.calls += 1
        if self.calls == 1:
            raise LLMBackendError("temporary failure")
        return LLMResponse(content="ok", model="gpt-test", backend="api")


class AlwaysFailingBackend:
    """Backend that always raises a backend error."""

    backend_name = "api"

    def __init__(self) -> None:
        """Initialize the call counter."""
        self.calls = 0

    def run(self, request: LLMRequest) -> LLMResponse:
        """Always raise a backend error."""
        self.calls += 1
        raise LLMBackendError("still failing")


class OkBackend:
    """Backend that always returns a successful response."""

    backend_name = "api"

    def run(self, request: LLMRequest) -> LLMResponse:
        """Return minimal JSON and non-zero token counts."""
        return LLMResponse(
            content="{}",
            model="gpt-test",
            backend="api",
            input_tokens=5,
            output_tokens=7,
            total_tokens=12,
        )


def _request() -> LLMRequest:
    """Create a reusable test request."""
    return LLMRequest(
        purpose="unit-test",
        system_prompt="You are concise.",
        user_prompt="Summarize this.",
        response_format={"type": "json_object"},
        max_output_tokens=100,
        metadata={"agent": "test"},
    )


def test_api_backend_posts_responses_payload_and_extracts_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API backend should call `/responses` and normalize usage data."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls: list[Mapping[str, Any]] = []

    def fake_post(**kwargs: Any) -> FakeHTTPResponse:
        calls.append(kwargs)
        return FakeHTTPResponse(
            {
                "model": "gpt-test",
                "output_text": "{\"ok\": true}",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 250,
                    "total_tokens": 1250,
                    "input_tokens_details": {"cached_tokens": 100},
                },
            }
        )

    config = LLMRunnerConfig(
        model="gpt-test",
        pricing_by_model={
            "gpt-test": ModelPricing(
                input_usd_per_million=1.0,
                cached_input_usd_per_million=0.1,
                output_usd_per_million=2.0,
            )
        },
    )
    backend = OpenAIAPIBackend(
        config,
        http_post=lambda url, headers, json, timeout: fake_post(
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        ),
    )

    response = backend.run(_request())

    assert response.content == "{\"ok\": true}"
    assert response.model == "gpt-test"
    assert response.backend == "api"
    assert response.input_tokens == 1000
    assert response.output_tokens == 250
    assert response.total_tokens == 1250
    assert response.estimated_cost_usd == pytest.approx(0.00141)
    assert calls[0]["url"] == "https://api.openai.com/v1/responses"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_api_backend_extracts_nested_response_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API backend should support nested Responses API content blocks."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIAPIBackend(
        LLMRunnerConfig(model="gpt-test"),
        http_post=lambda url, headers, json, timeout: FakeHTTPResponse(
            {
                "model": "gpt-test",
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "first"},
                            {"type": "output_text", "text": "second"},
                        ]
                    }
                ],
            }
        ),
    )

    assert backend.run(_request()).content == "first\nsecond"


def test_api_backend_wraps_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport errors should become retryable backend errors."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_post(**kwargs: Any) -> FakeHTTPResponse:
        raise requests.Timeout("slow")

    backend = OpenAIAPIBackend(
        LLMRunnerConfig(model="gpt-test"),
        http_post=lambda url, headers, json, timeout: fake_post(
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        ),
    )

    with pytest.raises(LLMBackendError, match="transport"):
        backend.run(_request())


def test_api_backend_does_not_retry_non_retryable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-transient HTTP status codes should fail as configuration errors."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIAPIBackend(
        LLMRunnerConfig(model="gpt-test"),
        http_post=lambda url, headers, json, timeout: FakeHTTPResponse(
            {"error": "bad"},
            status_code=401,
        ),
    )

    with pytest.raises(LLMConfigurationError, match="401"):
        backend.run(_request())


def test_api_backend_reports_missing_completion_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API responses without text should raise a backend error."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    backend = OpenAIAPIBackend(
        LLMRunnerConfig(model="gpt-test"),
        http_post=lambda url, headers, json, timeout: FakeHTTPResponse(
            {"model": "gpt-test"}
        ),
    )

    with pytest.raises(LLMBackendError, match="completion text"):
        backend.run(_request())


def test_api_backend_requires_explicit_model() -> None:
    """The API backend should fail early when no model is configured."""
    with pytest.raises(LLMConfigurationError, match="explicit model"):
        OpenAIAPIBackend(LLMRunnerConfig(model=None))


def test_api_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API backend should fail before network calls without an API key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    backend = OpenAIAPIBackend(LLMRunnerConfig(model="gpt-test"))

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        backend.run(_request())


def test_cursor_cli_backend_runs_agent_prompt() -> None:
    """The Cursor CLI backend should send the prompt via stdin by default."""
    commands: list[list[str]] = []
    inputs: list[str | None] = []
    environments: list[Mapping[str, str]] = []

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        inputs.append(input)
        environments.append(env)
        assert capture_output is True
        assert text is True
        assert timeout == 900
        assert check is False
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    backend = CursorCLIBackend(
        LLMRunnerConfig(backend="cursor_cli", model=None),
        subprocess_run=fake_run,
    )

    response = backend.run(_request())

    assert response.content == "done"
    assert response.model == "Auto"
    exe0, arg0 = commands[0][0:2]
    assert arg0 == "-p"
    assert exe0 == "agent" or Path(exe0).name.lower() == "agent.cmd"
    assert len(commands[0]) == 2
    assert inputs and "\"purpose\": \"unit-test\"" in str(inputs[0])
    assert "OPENAI_API_KEY" not in environments[0]


def test_cursor_cli_backend_can_use_argv_transport() -> None:
    """The Cursor CLI backend should support explicit argv prompt transport."""
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert input is None
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    backend = CursorCLIBackend(
        LLMRunnerConfig(
            backend="cursor_cli",
            cursor_prompt_transport="argv",
        ),
        subprocess_run=fake_run,
    )

    assert backend.run(_request()).content == "done"
    assert "\"purpose\": \"unit-test\"" in commands[0][2]


def test_cursor_cli_backend_reports_command_errors() -> None:
    """The Cursor CLI backend should expose command failure details."""

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="bad")

    backend = CursorCLIBackend(
        LLMRunnerConfig(backend="cursor_cli"),
        subprocess_run=fake_run,
    )

    with pytest.raises(LLMBackendError, match="exit code 2"):
        backend.run(_request())


def test_cursor_cli_backend_reports_empty_output() -> None:
    """A successful Cursor CLI process with empty stdout should fail."""

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    backend = CursorCLIBackend(
        LLMRunnerConfig(backend="cursor_cli"),
        subprocess_run=fake_run,
    )

    with pytest.raises(LLMBackendError, match="empty output"):
        backend.run(_request())


def test_cursor_cli_backend_reports_missing_command() -> None:
    """Missing Cursor CLI command should become a backend error."""

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    backend = CursorCLIBackend(
        LLMRunnerConfig(backend="cursor_cli"),
        subprocess_run=fake_run,
    )

    with pytest.raises(LLMBackendError, match="not found"):
        backend.run(_request())


def test_cursor_cli_backend_reports_timeout() -> None:
    """Cursor CLI timeout should become a backend error."""

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout)

    backend = CursorCLIBackend(
        LLMRunnerConfig(backend="cursor_cli"),
        subprocess_run=fake_run,
    )

    with pytest.raises(LLMBackendError, match="timed out"):
        backend.run(_request())


def test_runner_logs_completion_metrics_on_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each successful completion emits INFO with purpose, size, and timing."""
    caplog.set_level(logging.INFO, logger="tara.analysis.llm_runner")
    runner = LLMRunner(
        LLMRunnerConfig(model="gpt-test", max_retries=0),
        api_backend=OkBackend(),
    )
    runner.run(_request())
    joined = " | ".join(rec.message for rec in caplog.records)
    assert "llm_runner completed" in joined
    assert "purpose=unit-test" in joined
    assert "prompt_chars=" in joined
    assert "duration_ms=" in joined
    assert "total_tokens=12" in joined


def test_runner_retries_backend_and_records_telemetry() -> None:
    """The runner should retry transient backend errors and emit telemetry."""
    telemetry = FakeTelemetry()
    sleeps: list[float] = []
    backend = FlakyBackend()
    runner = LLMRunner(
        LLMRunnerConfig(
            model="gpt-test",
            max_retries=1,
            telemetry_metadata_keys=("agent",),
        ),
        telemetry=telemetry,
        api_backend=backend,
        sleep=sleeps.append,
    )

    response = runner.run(_request())

    assert response.content == "ok"
    assert backend.calls == 2
    assert sleeps == [1.0]
    assert telemetry.events == [
        (
            "llm_runner.usage.recorded",
            {
                "purpose": "unit-test",
                "backend": "api",
                "model": "gpt-test",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": None,
                "attempt": 2,
                "metadata": {"agent": "test"},
            },
        )
    ]


def test_runner_exhausts_configured_retries() -> None:
    """The runner should raise after the initial attempt plus retries."""
    sleeps: list[float] = []
    backend = AlwaysFailingBackend()
    runner = LLMRunner(
        LLMRunnerConfig(model="gpt-test", max_retries=2),
        api_backend=backend,
        sleep=sleeps.append,
    )

    with pytest.raises(LLMBackendError, match="failed after 3 attempts"):
        runner.run(_request())

    assert backend.calls == 3
    assert sleeps == [1.0, 2.0]


def test_runner_drops_unsafe_telemetry_metadata() -> None:
    """Telemetry should forward only configured safe metadata keys."""
    telemetry = FakeTelemetry()
    runner = LLMRunner(
        LLMRunnerConfig(model="gpt-test", telemetry_metadata_keys=("agent",)),
        telemetry=telemetry,
        api_backend=FlakyBackend(),
        sleep=lambda delay: None,
    )
    request = LLMRequest(
        purpose="unit-test",
        system_prompt="system",
        user_prompt="user",
        metadata={"agent": "test", "transcript_excerpt": "private"},
    )

    runner.run(request)

    assert telemetry.events[0][1]["metadata"] == {"agent": "test"}


def test_api_backend_uses_configured_api_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API key environment variable should be configurable."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "custom-key")

    def fake_post(**kwargs: Any) -> FakeHTTPResponse:
        assert kwargs["headers"]["Authorization"] == "Bearer custom-key"
        return FakeHTTPResponse({"model": "gpt-test", "output_text": "ok"})

    backend = OpenAIAPIBackend(
        LLMRunnerConfig(model="gpt-test", api_key_env="CUSTOM_API_KEY"),
        http_post=lambda url, headers, json, timeout: fake_post(
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        ),
    )

    assert backend.run(_request()).content == "ok"


def test_environment_is_not_required_for_cursor_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor CLI execution should not depend on OpenAI API credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        input: str | None,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    backend = CursorCLIBackend(
        LLMRunnerConfig(backend="cursor_cli"),
        subprocess_run=fake_run,
    )

    assert backend.run(_request()).content == "ok"
