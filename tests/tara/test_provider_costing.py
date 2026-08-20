from __future__ import annotations

import pytest

from tara.providers.costing import convert_native_cost


def test_cost_conversion_uses_decimal_micro_units() -> None:
    snapshot = convert_native_cost(
        "0.000015",
        native_currency="USD",
        eur_per_native="0.92",
        source="configured_estimate",
    )

    assert snapshot.native_cost_micros == 15
    assert snapshot.cost_micro_eur == 14
    assert snapshot.native_currency == "USD"
    assert snapshot.conversion_rate == "0.92"


@pytest.mark.parametrize("amount,rate", [("NaN", "1"), ("-1", "1"), ("1", "0")])
def test_cost_conversion_rejects_invalid_values(amount: str, rate: str) -> None:
    with pytest.raises(ValueError, match="cost configuration is invalid"):
        convert_native_cost(
            amount,
            native_currency="USD",
            eur_per_native=rate,
            source="configured_estimate",
        )
