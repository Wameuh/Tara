"""Deterministic provider-cost conversion for durable web telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_MICRO = Decimal("1000000")


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    cost_micro_eur: int
    native_cost_micros: int
    native_currency: str
    conversion_rate: str
    source: str


def convert_native_cost(
    amount: str | float | Decimal,
    *,
    native_currency: str,
    eur_per_native: str | Decimal,
    source: str,
) -> CostSnapshot:
    """Convert a provider-native amount using explicit decimal configuration."""
    try:
        native = Decimal(str(amount))
        rate = Decimal(str(eur_per_native))
    except InvalidOperation as exc:
        raise ValueError("cost configuration is invalid") from exc
    if (
        not native.is_finite()
        or native < 0
        or not rate.is_finite()
        or rate <= 0
        or rate > 100
    ):
        raise ValueError("cost configuration is invalid")
    native_micros = int((native * _MICRO).quantize(Decimal("1"), ROUND_HALF_UP))
    eur_micros = int((native * rate * _MICRO).quantize(Decimal("1"), ROUND_HALF_UP))
    return CostSnapshot(
        cost_micro_eur=eur_micros,
        native_cost_micros=native_micros,
        native_currency=native_currency,
        conversion_rate=format(rate, "f"),
        source=source,
    )
