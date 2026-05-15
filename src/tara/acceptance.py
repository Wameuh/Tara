"""Acceptance metrics for Tara analysis runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tara.analysis import ConflictSeverity, FactStatus, PipelineResult

_AGENT_LABEL = re.compile(r"\b[A-Za-z]+Agent\s*:", re.IGNORECASE)

_REQUIRED_MARKDOWN_SUBSTRINGS: tuple[str, ...] = (
    "# résumé de session",
    "## résumé express",
    "## impacts pour la suite",
    "## état final et ressources",
)

_LONG_BULLET_MIN_CHARS = 260
_MAX_AGENTIC_LONG_BULLETS = 5


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
    probe_llm_call_count: int
    analysis_llm_call_count: int
    composition_llm_call_count: int
    audit_llm_call_count: int
    estimated_llm_tokens: int
    estimated_cost_usd: float
    final_claim_count: int
    agentic_quality_satisfied: bool
    internal_agent_label_hits: int
    missing_required_markdown_sections: list[str]
    long_transcript_style_bullet_hits: int

    @property
    def accepted(self) -> bool:
        """Return whether hard acceptance invariants pass."""
        return (
            self.summary_support_rate == 1.0
            and self.forbidden_claim_leak_count == 0
            and self.unsupported_critical_claim_count == 0
            and self.affirmed_unresolved_critical_conflict_count == 0
            and self.agentic_quality_satisfied
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
            "probe_llm_call_count": self.probe_llm_call_count,
            "analysis_llm_call_count": self.analysis_llm_call_count,
            "composition_llm_call_count": self.composition_llm_call_count,
            "audit_llm_call_count": self.audit_llm_call_count,
            "estimated_llm_tokens": self.estimated_llm_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "final_claim_count": self.final_claim_count,
            "agentic_quality_satisfied": self.agentic_quality_satisfied,
            "internal_agent_label_hits": self.internal_agent_label_hits,
            "missing_required_markdown_sections": (
                self.missing_required_markdown_sections
            ),
            "long_transcript_style_bullet_hits": self.long_transcript_style_bullet_hits,
            "accepted": self.accepted,
        }


def evaluate_acceptance(
    result: PipelineResult,
    *,
    analysis_backend: str | None = None,
) -> AcceptanceReport:
    """Compute acceptance metrics for a pipeline result.

    Args:
        result: Completed analysis pipeline result.
        analysis_backend: Optional configured analysis backend label from Tara
            configuration. When ``api`` or ``cursor_cli``, additional quality
            gates apply for agentic runs.

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
    fs = result.final_summary
    agentic_backend = analysis_backend in {"api", "cursor_cli"}
    internal_hits = len(_AGENT_LABEL.findall(markdown))
    missing_sections = _missing_markdown_sections(markdown)
    long_bullets = _long_transcript_style_bullet_hits(markdown)
    agentic_ok = True
    if agentic_backend:
        if fs.analysis_llm_call_count < 1:
            agentic_ok = False
        if analysis_backend == "api" and fs.composition_llm_call_count < 1:
            agentic_ok = False
        if internal_hits > 0:
            agentic_ok = False
        if missing_sections:
            agentic_ok = False
        if long_bullets > _MAX_AGENTIC_LONG_BULLETS:
            agentic_ok = False
    return AcceptanceReport(
        summary_support_rate=support_rate,
        forbidden_claim_leak_count=forbidden_leaks,
        critical_claim_count=len(critical_facts),
        unsupported_critical_claim_count=len(unsupported_critical),
        critical_conflict_count=len(critical_conflicts),
        affirmed_unresolved_critical_conflict_count=affirmed_conflicts,
        llm_call_count=fs.llm_call_count,
        probe_llm_call_count=fs.probe_llm_call_count,
        analysis_llm_call_count=fs.analysis_llm_call_count,
        composition_llm_call_count=fs.composition_llm_call_count,
        audit_llm_call_count=fs.audit_llm_call_count,
        estimated_llm_tokens=fs.estimated_llm_tokens,
        estimated_cost_usd=fs.estimated_cost_usd or 0.0,
        final_claim_count=len(referenced_answer_ids),
        agentic_quality_satisfied=agentic_ok,
        internal_agent_label_hits=internal_hits,
        missing_required_markdown_sections=missing_sections,
        long_transcript_style_bullet_hits=long_bullets,
    )


def _long_transcript_style_bullet_hits(markdown: str) -> int:
    """Count markdown bullet lines long enough to suggest pasted raw transcript."""
    hits = 0
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") and len(stripped) >= _LONG_BULLET_MIN_CHARS:
            hits += 1
    return hits


def _missing_markdown_sections(markdown: str) -> list[str]:
    """Return required ARCHITECTURE headings missing from markdown."""
    lowered = markdown.casefold()
    missing: list[str] = []
    for required in _REQUIRED_MARKDOWN_SUBSTRINGS:
        if required not in lowered:
            missing.append(required)
    return missing


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
