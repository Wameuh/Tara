"""Acceptance metrics for Tara analysis runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tara.analysis import ConflictSeverity, FactStatus, PipelineResult


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    """Acceptance metrics computed from one pipeline result."""

    summary_support_rate: float
    forbidden_claim_leak_count: int
    critical_claim_count: int
    unsupported_critical_claim_count: int
    critical_conflict_count: int
    affirmed_unresolved_critical_conflict_count: int
    llm_call_count: int
    estimated_llm_tokens: int
    estimated_cost_usd: float
    final_claim_count: int

    @property
    def accepted(self) -> bool:
        """Return whether hard acceptance invariants pass."""
        return (
            self.summary_support_rate == 1.0
            and self.forbidden_claim_leak_count == 0
            and self.unsupported_critical_claim_count == 0
            and self.affirmed_unresolved_critical_conflict_count == 0
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a JSON-compatible dictionary."""
        return {
            "summary_support_rate": self.summary_support_rate,
            "forbidden_claim_leak_count": self.forbidden_claim_leak_count,
            "critical_claim_count": self.critical_claim_count,
            "unsupported_critical_claim_count": self.unsupported_critical_claim_count,
            "critical_conflict_count": self.critical_conflict_count,
            "affirmed_unresolved_critical_conflict_count": (
                self.affirmed_unresolved_critical_conflict_count
            ),
            "llm_call_count": self.llm_call_count,
            "estimated_llm_tokens": self.estimated_llm_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "final_claim_count": self.final_claim_count,
            "accepted": self.accepted,
        }


def evaluate_acceptance(result: PipelineResult) -> AcceptanceReport:
    """Compute acceptance metrics for a pipeline result.

    Args:
        result: Completed analysis pipeline result.

    Returns:
        Acceptance metrics used by tests and manual Record19 evaluation.
    """
    usable_answer_ids = {
        fact.answer_id
        for fact in result.blackboard.facts
        if fact.answer_id
        and fact.status == FactStatus.SUPPORTED
        and not fact.do_not_claim
    }
    referenced_answer_ids = [
        answer_id
        for section in result.final_summary.sections
        for answer_id in section.supporting_answer_ids
    ]
    supported_references = [
        answer_id
        for answer_id in referenced_answer_ids
        if answer_id in usable_answer_ids
    ]
    support_rate = (
        len(supported_references) / len(referenced_answer_ids)
        if referenced_answer_ids
        else 1.0
    )
    markdown = result.final_summary.markdown
    forbidden_leaks = sum(
        1
        for claim in result.blackboard.do_not_claim_list
        if claim and claim in markdown
    )
    critical_facts = [
        fact
        for fact in result.blackboard.facts
        if fact.status == FactStatus.SUPPORTED
        and not fact.do_not_claim
        and (fact.is_critical or fact.importance >= 4)
    ]
    unsupported_critical = [fact for fact in critical_facts if not fact.support]
    critical_conflicts = [
        conflict
        for conflict in result.blackboard.conflicts
        if conflict.severity == ConflictSeverity.CRITICAL
    ]
    affirmed_conflicts = _affirmed_unresolved_critical_conflicts(
        result=result,
        critical_answer_ids={
            answer_id
            for conflict in critical_conflicts
            for answer_id in conflict.answer_ids
        },
    )
    return AcceptanceReport(
        summary_support_rate=support_rate,
        forbidden_claim_leak_count=forbidden_leaks,
        critical_claim_count=len(critical_facts),
        unsupported_critical_claim_count=len(unsupported_critical),
        critical_conflict_count=len(critical_conflicts),
        affirmed_unresolved_critical_conflict_count=affirmed_conflicts,
        llm_call_count=result.final_summary.llm_call_count,
        estimated_llm_tokens=result.final_summary.estimated_llm_tokens,
        estimated_cost_usd=result.final_summary.estimated_cost_usd or 0.0,
        final_claim_count=len(referenced_answer_ids),
    )


def _affirmed_unresolved_critical_conflicts(
    *,
    result: PipelineResult,
    critical_answer_ids: set[str],
) -> int:
    """Count unresolved critical conflict answers that remain affirmed."""
    if not critical_answer_ids:
        return 0
    final_answer_ids = {
        answer_id
        for section in result.final_summary.sections
        for answer_id in section.supporting_answer_ids
    }
    return len(final_answer_ids & critical_answer_ids)
