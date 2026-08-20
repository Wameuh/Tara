from __future__ import annotations

import pytest

from tara_web.services.costs import aggregate_attempt_costs


def test_cost_aggregation_distinguishes_complete_partial_and_unavailable() -> None:
    assert aggregate_attempt_costs(
        [{"provider_cost_micro_eur": 123, "cost_known": 1, "has_known_cost": 1}]
    ) == {
        "status": "complete",
        "value_micro_eur": 123,
        "explanation_key": "result.cost_explanation",
    }
    assert aggregate_attempt_costs(
        [
            {"provider_cost_micro_eur": 12, "cost_known": 1, "has_known_cost": 1},
            {"provider_cost_micro_eur": 0, "cost_known": 0, "has_known_cost": 0},
        ]
    )["status"] == "partial"
    assert aggregate_attempt_costs([])["status"] == "unavailable"


def test_cost_aggregation_rejects_negative_amounts() -> None:
    with pytest.raises(ValueError):
        aggregate_attempt_costs(
            [{"provider_cost_micro_eur": -1, "cost_known": 1, "has_known_cost": 1}]
        )
