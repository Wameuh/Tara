"""Closed, transport-safe provider attempt telemetry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from tara.web_contracts import CONTRACT_VERSION, EventType, RunnerEvent

_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_NAME = re.compile(r"^[a-z0-9_-]{1,64}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_STATUSES = frozenset({"success", "failed", "cancelled", "timed_out"})
_MAX_COST_MICRO_EUR = 10**15
_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class UsageAttempt:
    attempt_id: str
    operation_family: str
    provider: str
    model: str | None
    status: str
    started_at: str
    finished_at: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    duration_ms: int = 0
    cost_micro_eur: int | None = None
    cost_source: str = "unavailable"
    native_cost_micros: int | None = None
    native_currency: str | None = None
    conversion_rate: str | None = None

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.attempt_id):
            raise ValueError("provider attempt is invalid")
        if (
            self.status not in _STATUSES
            or not _NAME.fullmatch(self.operation_family)
            or not _NAME.fullmatch(self.provider)
        ):
            raise ValueError("provider attempt is invalid")
        if self.model is not None and not _MODEL.fullmatch(self.model):
            raise ValueError("provider attempt is invalid")
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.cache_tokens,
            self.duration_ms,
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 10**12
            ):
                raise ValueError("provider attempt is invalid")
        if self.cost_micro_eur is not None and (
            not isinstance(self.cost_micro_eur, int)
            or isinstance(self.cost_micro_eur, bool)
            or not 0 <= self.cost_micro_eur <= _MAX_COST_MICRO_EUR
        ):
            raise ValueError("provider attempt is invalid")
        if not _NAME.fullmatch(self.cost_source):
            raise ValueError("provider attempt is invalid")
        _validate_native_cost(self)
        started = _utc(self.started_at)
        finished = _utc(self.finished_at)
        if finished < started:
            raise ValueError("provider attempt is invalid")

    def parameters(self) -> dict[str, str | int]:
        return {
            "attempt_id": self.attempt_id,
            "operation_family": self.operation_family,
            "provider": self.provider,
            "model": self.model or "",
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_tokens": self.cache_tokens,
            "duration_ms": self.duration_ms,
            "cost_micro_eur": (
                self.cost_micro_eur if self.cost_micro_eur is not None else -1
            ),
            "cost_source": self.cost_source,
            "native_cost_micros": (
                self.native_cost_micros if self.native_cost_micros is not None else -1
            ),
            "native_currency": self.native_currency or "",
            "conversion_rate": self.conversion_rate or "",
        }

    def to_runner_event(self, revision: int) -> RunnerEvent:
        return RunnerEvent(
            CONTRACT_VERSION,
            EventType.USAGE_RECORDED,
            revision,
            parameters=self.parameters(),
        )

    @classmethod
    def from_runner_event(cls, event: RunnerEvent) -> UsageAttempt:
        if event.event_type is not EventType.USAGE_RECORDED:
            raise ValueError("provider attempt is invalid")
        values = event.parameters
        required = {
            "attempt_id",
            "operation_family",
            "provider",
            "model",
            "status",
            "started_at",
            "finished_at",
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "duration_ms",
            "cost_micro_eur",
            "cost_source",
            "native_cost_micros",
            "native_currency",
            "conversion_rate",
        }
        if set(values) != required:
            raise ValueError("provider attempt is invalid")
        cost = values["cost_micro_eur"]
        return cls(
            attempt_id=_string(values["attempt_id"]),
            operation_family=_string(values["operation_family"]),
            provider=_string(values["provider"]),
            model=_string(values["model"]) or None,
            status=_string(values["status"]),
            started_at=_string(values["started_at"]),
            finished_at=_string(values["finished_at"]),
            input_tokens=_integer(values["input_tokens"]),
            output_tokens=_integer(values["output_tokens"]),
            cache_tokens=_integer(values["cache_tokens"]),
            duration_ms=_integer(values["duration_ms"]),
            cost_micro_eur=None if cost == -1 else _integer(cost),
            cost_source=_string(values["cost_source"]),
            native_cost_micros=(
                None
                if values["native_cost_micros"] == -1
                else _integer(values["native_cost_micros"])
            ),
            native_currency=_string(values["native_currency"]) or None,
            conversion_rate=_string(values["conversion_rate"]) or None,
        )


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
        raise ValueError("provider attempt is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("provider attempt is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("provider attempt is invalid")
    return parsed


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("provider attempt is invalid")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("provider attempt is invalid")
    return value


def _validate_native_cost(attempt: UsageAttempt) -> None:
    values = (
        attempt.native_cost_micros,
        attempt.native_currency,
        attempt.conversion_rate,
        attempt.cost_micro_eur,
    )
    if all(value is None for value in values):
        return
    if any(value is None for value in values):
        raise ValueError("provider attempt is invalid")
    assert attempt.native_cost_micros is not None
    assert attempt.native_currency is not None
    assert attempt.conversion_rate is not None
    if (
        not isinstance(attempt.native_cost_micros, int)
        or isinstance(attempt.native_cost_micros, bool)
        or not 0 <= attempt.native_cost_micros <= _MAX_COST_MICRO_EUR
        or not _CURRENCY.fullmatch(attempt.native_currency)
        or len(attempt.conversion_rate) > 32
    ):
        raise ValueError("provider attempt is invalid")
    try:
        rate = Decimal(attempt.conversion_rate)
    except InvalidOperation as exc:
        raise ValueError("provider attempt is invalid") from exc
    if not rate.is_finite() or not Decimal("0") < rate <= Decimal("100"):
        raise ValueError("provider attempt is invalid")
