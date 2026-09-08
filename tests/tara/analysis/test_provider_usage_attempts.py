from __future__ import annotations

from collections.abc import Callable

import pytest

from tara.analysis.llm_runner import (
    LLMBackendError,
    LLMRequest,
    LLMResponse,
    LLMRunner,
    LLMRunnerConfig,
)
from tara.providers.events import UsageAttempt


class _Backend:
    backend_name = "api"

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def run(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


def _response() -> LLMResponse:
    return LLMResponse(
        "ok",
        "gpt-5.4",
        "api",
        input_tokens=12,
        output_tokens=4,
        raw_usage={"cache_read_tokens": 3},
    )


def _runner(
    outcomes: list[object],
    attempts: list[UsageAttempt],
    *,
    retries: int = 0,
    cancellation_check: Callable[[], None] | None = None,
    callback: Callable[[UsageAttempt], None] | None = None,
) -> tuple[LLMRunner, _Backend]:
    backend = _Backend(outcomes)
    return (
        LLMRunner(
            LLMRunnerConfig(backend="api", model="gpt-5.4", max_retries=retries),
            api_backend=backend,
            sleep=lambda _: None,
            cancellation_check=cancellation_check,
            usage_attempt_callback=callback or attempts.append,
        ),
        backend,
    )


def _request() -> LLMRequest:
    return LLMRequest("test", "system", "user")


def test_success_attempt_records_available_usage() -> None:
    attempts: list[UsageAttempt] = []
    runner, _ = _runner([_response()], attempts)

    assert runner.run(_request()).content == "ok"
    assert len(attempts) == 1
    assert attempts[0].status == "success"
    assert (attempts[0].input_tokens, attempts[0].output_tokens) == (12, 4)
    assert attempts[0].cache_tokens == 3
    assert attempts[0].cost_micro_eur is None


def test_attempt_classifies_prompt_purpose_without_storing_prompt_text() -> None:
    attempts: list[UsageAttempt] = []
    runner, _ = _runner([_response()], attempts)

    runner.run(
        LLMRequest(
            "analysis.scenes.describe.yaml_repair",
            "private system prompt",
            "private user prompt",
        )
    )

    assert attempts[0].operation_family == "llm"
    assert attempts[0].operation_name == "scene_descriptions"
    assert "private" not in str(attempts[0].parameters())


def test_success_attempt_snapshots_native_cost_and_conversion_rate() -> None:
    attempts: list[UsageAttempt] = []
    response = _response()
    response = LLMResponse(
        response.content,
        response.model,
        response.backend,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        raw_usage=response.raw_usage,
        estimated_cost_usd=0.000015,
    )
    backend = _Backend([response])
    runner = LLMRunner(
        LLMRunnerConfig(
            backend="api",
            model="gpt-5.4",
            usd_to_eur_rate="0.92",
        ),
        api_backend=backend,
        usage_attempt_callback=attempts.append,
    )

    runner.run(_request())
    attempt = attempts[0]
    assert (
        attempt.cost_micro_eur,
        attempt.native_cost_micros,
        attempt.native_currency,
        attempt.conversion_rate,
    ) == (14, 15, "USD", "0.92")
    assert attempt.cost_source == "configured_estimate"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (LLMBackendError("provider secret"), "failed"),
        (TimeoutError("provider secret"), "timed_out"),
        (RuntimeError("provider secret"), "failed"),
    ],
)
def test_failed_attempt_statuses_never_persist_error_text(
    error: BaseException, status: str
) -> None:
    attempts: list[UsageAttempt] = []
    runner, _ = _runner([error], attempts)

    with pytest.raises(type(error)):
        runner.run(_request())
    assert len(attempts) == 1
    assert attempts[0].status == status
    assert "secret" not in str(attempts[0].parameters())


def test_cancellation_before_provider_creates_no_attempt() -> None:
    attempts: list[UsageAttempt] = []

    def cancel() -> None:
        raise LookupError("cancel")

    runner, backend = _runner([_response()], attempts, cancellation_check=cancel)
    with pytest.raises(LookupError, match="cancel"):
        runner.run(_request())
    assert backend.calls == 0
    assert attempts == []


def test_cancellation_after_provider_records_cancelled_usage() -> None:
    attempts: list[UsageAttempt] = []
    checks = 0

    def cancel_after() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise LookupError("cancel")

    runner, backend = _runner([_response()], attempts, cancellation_check=cancel_after)
    with pytest.raises(LookupError, match="cancel"):
        runner.run(_request())
    assert backend.calls == 1
    assert [(item.status, item.input_tokens) for item in attempts] == [
        ("cancelled", 12)
    ]


def test_retry_records_one_unique_attempt_per_backend_call() -> None:
    attempts: list[UsageAttempt] = []
    runner, backend = _runner(
        [LLMBackendError("temporary"), _response()], attempts, retries=1
    )

    assert runner.run(_request()).content == "ok"
    assert backend.calls == 2
    assert [item.status for item in attempts] == ["failed", "success"]
    assert len({item.attempt_id for item in attempts}) == 2


def test_usage_callback_failure_does_not_mask_provider_failure() -> None:
    def broken(_: UsageAttempt) -> None:
        raise RuntimeError("telemetry unavailable")

    attempts: list[UsageAttempt] = []
    runner, _ = _runner(
        [LLMBackendError("provider failure")], attempts, callback=broken
    )
    with pytest.raises(LLMBackendError, match="failed after 1 attempts"):
        runner.run(_request())


def test_usage_callback_failure_rejects_successful_provider_result() -> None:
    def broken(_: UsageAttempt) -> None:
        raise RuntimeError("telemetry unavailable")

    attempts: list[UsageAttempt] = []
    runner, _ = _runner([_response()], attempts, callback=broken)
    with pytest.raises(RuntimeError, match="telemetry unavailable"):
        runner.run(_request())
