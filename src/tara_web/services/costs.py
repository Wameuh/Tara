"""Public-safe aggregation of attempt costs in integer micro-euros."""

from __future__ import annotations


def aggregate_attempt_costs(attempts: object) -> dict[str, object]:
    rows = tuple(attempts)
    if any(
        int(row["provider_cost_micro_eur"]) < 0
        for row in rows
    ):
        raise ValueError("attempt cost is invalid")
    complete = [
        int(row["provider_cost_micro_eur"]) for row in rows if bool(row["cost_known"])
    ]
    available = [
        int(row["provider_cost_micro_eur"])
        for row in rows
        if bool(row["has_known_cost"]) or bool(row["cost_known"])
    ]
    if rows and len(complete) == len(rows):
        return {
            "status": "complete",
            "value_micro_eur": sum(complete),
            "explanation_key": "result.cost_explanation",
        }
    if available:
        return {
            "status": "partial",
            "value_micro_eur": sum(available),
            "explanation_key": "result.cost_partial_explanation",
        }
    return {
        "status": "unavailable",
        "value_micro_eur": None,
        "explanation_key": "result.cost_unavailable_explanation",
    }
