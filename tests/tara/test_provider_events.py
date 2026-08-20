from __future__ import annotations

from dataclasses import replace

import pytest

from tara.providers.events import UsageAttempt
from tara.web_contracts import CONTRACT_VERSION, EventType, RunnerEvent


def _attempt(**updates: object) -> UsageAttempt:
    values: dict[str, object] = {
        "attempt_id": "pa_abcdefghijklmnop",
        "operation_family": "llm",
        "provider": "cursor_cli",
        "model": "nvidia/parakeet:tdt-0.6b-v3",
        "status": "success",
        "started_at": "2026-07-18T10:00:00Z",
        "finished_at": "2026-07-18T10:00:01Z",
        "input_tokens": 12,
        "output_tokens": 4,
        "cache_tokens": 3,
        "duration_ms": 1_000,
        "cost_micro_eur": None,
        "cost_source": "unavailable",
        "native_cost_micros": None,
        "native_currency": None,
        "conversion_rate": None,
    }
    values.update(updates)
    return UsageAttempt(**values)  # type: ignore[arg-type]


def test_usage_attempt_event_round_trip_is_closed() -> None:
    attempt = _attempt()
    event = attempt.to_runner_event(7)

    assert event.event_type is EventType.USAGE_RECORDED
    assert event.revision == 7
    assert UsageAttempt.from_runner_event(event) == attempt

    parameters = dict(event.parameters)
    parameters.pop("provider")
    parameters["error"] = "secret provider path"
    hostile = RunnerEvent(
        CONTRACT_VERSION,
        EventType.USAGE_RECORDED,
        8,
        parameters=parameters,
    )
    with pytest.raises(ValueError, match="provider attempt is invalid"):
        UsageAttempt.from_runner_event(hostile)


@pytest.mark.parametrize(
    "updates",
    [
        {"started_at": "2026-07-18T10:00:00"},
        {"started_at": "2026-07-18T10:00:02Z"},
        {"model": r"C:\\private\\secret"},
        {"input_tokens": True},
        {"cost_micro_eur": 10**15 + 1},
        {"cost_micro_eur": 1, "native_cost_micros": None},
        {
            "cost_micro_eur": 1,
            "native_cost_micros": 1,
            "native_currency": "usd",
            "conversion_rate": "0.92",
        },
        {"status": "unknown"},
    ],
)
def test_usage_attempt_rejects_hostile_or_ambiguous_values(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="provider attempt is invalid"):
        _attempt(**updates)


def test_usage_attempt_rejects_wrong_event_type() -> None:
    event = replace(
        _attempt().to_runner_event(1),
        event_type=EventType.RUN_COMPLETED,
    )
    with pytest.raises(ValueError, match="provider attempt is invalid"):
        UsageAttempt.from_runner_event(event)
