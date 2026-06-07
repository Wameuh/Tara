"""Deterministic and agentic agent layer for the Tara blackboard analysis pipeline."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from tara.analysis.agentic_llm import (
    LLMUsageDelta,
    blackboard_facts_to_payload,
    run_arbitration_llm,
    run_audit_llm,
    run_composer_llm,
    run_specialist_extraction,
)
from tara.analysis.llm_runner import LLMRunner
from tara.analysis.models import (
    AnalysisPlan,
    AnalysisQuestion,
    ArbitrationDecision,
    AuditFinding,
    BlackboardFact,
    ClaimType,
    Confidence,
    Conflict,
    ConflictSeverity,
    EvidenceAnswer,
    EvidenceSupport,
    FactStatus,
    FinalSummary,
    JsonObject,
    RetrievalQuery,
    RetrievedEvidence,
    SummaryDraft,
    SummarySection,
)
from tara.analysis.structured_output import ArbitrationLLMVerdict

LOGGER = logging.getLogger(__name__)


class EvidenceRetriever(Protocol):
    """Protocol for retrieval implementations used by specialist agents."""

    def retrieve(
        self,
        query: RetrievalQuery,
        limit: int | None = None,
    ) -> list[RetrievedEvidence]:
        """Retrieve evidence for a structured query."""


@dataclass(slots=True)
class BlackboardState:
    """Validated blackboard state shared across downstream agents."""

    facts: list[BlackboardFact]
    conflicts: list[Conflict]
    do_not_claim_list: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    """Result of a bounded analysis orchestration run."""

    plan: AnalysisPlan
    answers: list[EvidenceAnswer]
    blackboard: BlackboardState
    decisions: list[ArbitrationDecision]
    draft: SummaryDraft
    findings: list[AuditFinding]
    final_summary: FinalSummary
    attempts: int


class AnalysisPlannerAgent:
    """Create deterministic analysis questions for the standard summary needs."""

    def plan(self, audit_feedback: list[AuditFinding] | None = None) -> AnalysisPlan:
        """Create an analysis plan.

        Args:
            audit_feedback: Optional findings from a previous audit attempt.

        Returns:
            Analysis plan with standard specialist questions.
        """
        feedback_metadata = {
            "audit_feedback_count": len(audit_feedback or []),
        }
        questions = [
            _question(
                "chronology",
                "ChronologyAgent",
                ClaimType.CHRONOLOGY,
                [
                    "chronologie evenement majeur",
                    "mort sanctuaire temple",
                    "debut milieu fin session ordre des scenes",
                ],
                priority=1,
            ),
            _question(
                "combat_outcome",
                "CombatOutcomeAgent",
                ClaimType.COMBAT_OUTCOME,
                ["mort inconscient stabilise soin ennemi", "combat menace"],
                priority=1,
                risk_level=ConflictSeverity.CRITICAL,
            ),
            _question(
                "character_state",
                "CharacterStateAgent",
                ClaimType.CHARACTER_STATE,
                [
                    "etat final personnage position ressource",
                    "potion soin blessure",
                    "fin session blessure position groupe",
                ],
                priority=1,
                risk_level=ConflictSeverity.MAJOR,
            ),
            _question(
                "quest_continuity",
                "QuestContinuityAgent",
                ClaimType.QUEST_CONTINUITY,
                [
                    "lieu objet mecanisme sanctuaire temple",
                    "consequence prochaine session",
                    "suite immediate et resolution des consequences",
                ],
                priority=2,
            ),
            _question(
                "resource_state",
                "QuestContinuityAgent",
                ClaimType.RESOURCE_STATE,
                ["ressource potion lance objet", "gain depense ressource"],
                priority=2,
            ),
            _question(
                "uncertainty",
                "UncertaintyAgent",
                ClaimType.FINAL_STATE,
                ["incertain contradictoire non confirme interdit", "ambigu doute"],
                priority=3,
                risk_level=ConflictSeverity.MAJOR,
            ),
        ]
        return AnalysisPlan(questions=questions, metadata=feedback_metadata)


class SpecialistAgent:
    """Specialist that answers from retrieved chunks, optionally via LLM extraction."""

    agent_name: str = "SpecialistAgent"
    claim_type: ClaimType = ClaimType.CHRONOLOGY

    def __init__(
        self,
        llm_runner: object | None = None,
        config: JsonObject | None = None,
    ) -> None:
        """Initialize a specialist agent.

        Args:
            llm_runner: Optional LLM runner for agentic extraction.
            config: Optional JSON-compatible agent configuration.
        """
        self.llm_runner = llm_runner
        self.config = config or {}
        self.last_llm_usage = LLMUsageDelta()

    def _agentic_backend_enabled(self) -> bool:
        """Return True when this specialist should call the LLM."""
        backend = str(self.config.get("backend", "deterministic"))
        if self.llm_runner is None:
            return False
        if backend == "api":
            return True
        if backend == "cursor_cli":
            return bool(self.config.get("cursor_cli_probe"))
        return False

    def answer(
        self,
        question: AnalysisQuestion,
        retriever: EvidenceRetriever,
        limit_per_query: int = 3,
    ) -> list[EvidenceAnswer]:
        """Answer an analysis question using retrieved chunks.

        Args:
            question: Planned question assigned to the specialist.
            retriever: Evidence retriever.
            limit_per_query: Maximum evidence chunks per retrieval query.

        Returns:
            Sourced evidence answers. If retrieval returns nothing, a single
            uncertain answer is emitted instead of hallucinated facts.
        """
        self.last_llm_usage = LLMUsageDelta()
        results: list[RetrievedEvidence] = []
        for query in question.retrieval_queries:
            results.extend(retriever.retrieve(query, limit=limit_per_query))
        deduped = _dedupe_results(results)
        if not deduped:
            return [
                EvidenceAnswer(
                    answer_id=f"{question.question_id}_uncertain",
                    question_id=question.question_id,
                    claim=f"No sourced evidence found for {question.question_id}.",
                    status=FactStatus.UNCERTAIN,
                    importance=question.priority,
                    confidence=Confidence.LOW,
                    claim_type=_question_claim_type(question, self.claim_type),
                    notes="Deterministic fallback after empty retrieval.",
                )
            ]
        if self._agentic_backend_enabled():
            runner = cast(LLMRunner, self.llm_runner)
            chunks = [item.chunk for item in deduped]
            answers, usage = run_specialist_extraction(
                runner,
                question=question,
                chunks=chunks,
                default_claim_type=_question_claim_type(question, self.claim_type),
                is_critical_default=question.risk_level == ConflictSeverity.CRITICAL,
                context_text=_config_text(self.config, "context_text"),
            )
            self.last_llm_usage = usage
            if len(answers) == 1 and answers[0].status == FactStatus.UNCERTAIN:
                return [
                    self._answer_from_evidence(question, result, index)
                    for index, result in enumerate(deduped)
                ]
            return self._postprocess_llm_answers(question, answers)
        return [
            self._answer_from_evidence(question, result, index)
            for index, result in enumerate(deduped)
        ]

    def _postprocess_llm_answers(
        self,
        question: AnalysisQuestion,
        answers: list[EvidenceAnswer],
    ) -> list[EvidenceAnswer]:
        """Allow subclasses to adjust LLM answers before returning."""
        return answers

    def _answer_from_evidence(
        self,
        question: AnalysisQuestion,
        evidence: RetrievedEvidence,
        index: int,
    ) -> EvidenceAnswer:
        """Create one sourced answer from retrieved evidence."""
        chunk = evidence.chunk
        support = [
            EvidenceSupport(
                chunk_id=chunk.chunk_id,
                start=chunk.start,
                end=chunk.end,
                segment_ids=chunk.segment_ids,
            )
        ]
        importance = 4 if question.risk_level != ConflictSeverity.MINOR else 3
        claim_type = _question_claim_type(question, self.claim_type)
        return EvidenceAnswer(
            answer_id=f"{question.question_id}_{index:02d}",
            question_id=question.question_id,
            claim=_short_text(chunk.text),
            status=FactStatus.SUPPORTED,
            importance=importance,
            support=support,
            confidence=Confidence.MEDIUM,
            claim_type=claim_type,
            is_critical=question.risk_level == ConflictSeverity.CRITICAL,
            metadata={"source": "retrieval", "score": evidence.score},
        )


class ChronologyAgent(SpecialistAgent):
    """Specialist for chronological events."""

    agent_name = "ChronologyAgent"
    claim_type = ClaimType.CHRONOLOGY


class CombatOutcomeAgent(SpecialistAgent):
    """Specialist for combat outcomes and threats."""

    agent_name = "CombatOutcomeAgent"
    claim_type = ClaimType.COMBAT_OUTCOME


class CharacterStateAgent(SpecialistAgent):
    """Specialist for final character state and resources."""

    agent_name = "CharacterStateAgent"
    claim_type = ClaimType.CHARACTER_STATE


class QuestContinuityAgent(SpecialistAgent):
    """Specialist for locations, objects, mechanisms, and continuity."""

    agent_name = "QuestContinuityAgent"
    claim_type = ClaimType.QUEST_CONTINUITY


class UncertaintyAgent(SpecialistAgent):
    """Specialist for uncertain or forbidden claims."""

    agent_name = "UncertaintyAgent"
    claim_type = ClaimType.FINAL_STATE

    def _postprocess_llm_answers(
        self,
        question: AnalysisQuestion,
        answers: list[EvidenceAnswer],
    ) -> list[EvidenceAnswer]:
        """Reject answers that restate explicitly forbidden transcript cues."""
        adjusted: list[EvidenceAnswer] = []
        for answer in answers:
            if "interdit" in answer.claim.lower():
                adjusted.append(
                    answer.model_copy(
                        update={
                            "status": FactStatus.REJECTED,
                            "confidence": Confidence.HIGH,
                            "notes": "Claim explicitly marked as forbidden.",
                        },
                    ),
                )
            else:
                adjusted.append(answer)
        return adjusted

    def _answer_from_evidence(
        self,
        question: AnalysisQuestion,
        evidence: RetrievedEvidence,
        index: int,
    ) -> EvidenceAnswer:
        """Create an uncertainty answer without promoting unsupported claims."""
        answer = super()._answer_from_evidence(question, evidence, index)
        if "interdit" in evidence.chunk.text.lower():
            return answer.model_copy(
                update={
                    "status": FactStatus.REJECTED,
                    "confidence": Confidence.HIGH,
                    "notes": "Claim explicitly marked as forbidden.",
                },
            )
        return answer


class BlackboardController:
    """Validate, deduplicate, classify, and conflict-check evidence answers."""

    def ingest(self, answers: list[EvidenceAnswer]) -> BlackboardState:
        """Build a blackboard state from specialist answers.

        Args:
            answers: Specialist answers.

        Returns:
            Blackboard state with facts, conflicts, and do-not-claim entries.
        """
        facts: list[BlackboardFact] = []
        seen_claims: set[str] = set()
        do_not_claim: list[str] = []
        for answer in answers:
            normalized = _normalize_claim(answer.claim)
            if normalized in seen_claims:
                continue
            seen_claims.add(normalized)
            do_not = answer.status in {FactStatus.REJECTED, FactStatus.UNCERTAIN}
            if do_not:
                do_not_claim.append(answer.claim)
            facts.append(
                BlackboardFact(
                    fact_id=f"fact_{len(facts):04d}",
                    answer_id=answer.answer_id,
                    claim=answer.claim,
                    status=answer.status,
                    confidence=answer.confidence,
                    importance=answer.importance,
                    claim_type=answer.claim_type,
                    support=answer.support,
                    is_critical=answer.is_critical,
                    do_not_claim=do_not,
                    metadata=dict(answer.metadata),
                )
            )
        self._mark_ambiguous_actor_event_claims(facts, do_not_claim)
        conflicts = self._detect_conflicts(facts)
        return BlackboardState(
            facts=facts,
            conflicts=conflicts,
            do_not_claim_list=do_not_claim,
        )

    @staticmethod
    def _mark_ambiguous_actor_event_claims(
        facts: list[BlackboardFact],
        do_not_claim: list[str],
    ) -> None:
        """Prefer neutral event facts when actor-specific variants conflict."""
        for event_key in _ambiguous_actor_event_keys(facts):
            event_facts = [
                fact
                for fact in facts
                if fact.status == FactStatus.SUPPORTED
                and not fact.do_not_claim
                and _actor_event_key(fact.claim) == event_key
            ]
            if not any(_has_neutral_actor(fact.claim) for fact in event_facts):
                continue
            for fact in event_facts:
                if _has_neutral_actor(fact.claim):
                    continue
                fact.do_not_claim = True
                do_not_claim.append(fact.claim)

    @staticmethod
    def _detect_conflicts(facts: list[BlackboardFact]) -> list[Conflict]:
        """Detect competing critical claims, skipping complementary duplicates."""
        conflicts: list[Conflict] = []
        grouped: dict[ClaimType, list[BlackboardFact]] = {}
        for fact in facts:
            if (
                fact.claim_type is None
                or fact.status != FactStatus.SUPPORTED
                or fact.do_not_claim
            ):
                continue
            grouped.setdefault(fact.claim_type, []).append(fact)
        for claim_type, candidates in grouped.items():
            critical = [fact for fact in candidates if fact.importance >= 4]
            if len(critical) < 2:
                continue
            normalized = {_normalize_claim(fact.claim) for fact in critical}
            if len(normalized) <= 1:
                continue
            if _critical_claims_are_complementary([fact.claim for fact in critical]):
                continue
            severity = (
                ConflictSeverity.CRITICAL
                if any(fact.is_critical or fact.importance >= 5 for fact in critical)
                else ConflictSeverity.MAJOR
            )
            conflicts.append(
                Conflict(
                    conflict_id=f"conflict_{claim_type.value}",
                    answer_ids=[
                        fact.answer_id
                        for fact in critical
                        if fact.answer_id is not None
                    ],
                    severity=severity,
                    description=f"Competing critical {claim_type.value} claims.",
                ),
            )
        return conflicts


class ArbitrationPanel:
    """Apply arbitration policies before composition."""

    def __init__(self) -> None:
        """Initialize the arbitration panel."""
        self.last_llm_usage = LLMUsageDelta()

    def arbitrate(
        self,
        blackboard: BlackboardState,
        *,
        llm_runner: LLMRunner | None = None,
        specialist_backend: str = "deterministic",
        cursor_cli_probe: bool = False,
        retriever: EvidenceRetriever | None = None,
        context_text: str | None = None,
    ) -> list[ArbitrationDecision]:
        """Resolve or quarantine conflicts.

        Args:
            blackboard: Current blackboard state.
            llm_runner: Optional runner for semantic arbitration.
            specialist_backend: Active analysis backend label.
            retriever: Optional evidence index for chunk snippets.

        Returns:
            Arbitration decisions.
        """
        self.last_llm_usage = LLMUsageDelta()
        decisions: list[ArbitrationDecision] = []
        agentic = llm_runner is not None and (
            specialist_backend == "api"
            or (specialist_backend == "cursor_cli" and cursor_cli_probe)
        )
        for conflict in blackboard.conflicts:
            if agentic:
                rows = _conflict_fact_rows(conflict, blackboard, retriever)
                verdict, usage = run_arbitration_llm(
                    cast(LLMRunner, llm_runner),
                    conflict=conflict,
                    fact_rows=rows,
                    context_text=context_text,
                )
                self.last_llm_usage = _merge_usage_delta(self.last_llm_usage, usage)
                if verdict is not None and not verdict.is_contradiction:
                    continue
                if verdict is not None and verdict.outcome in {
                    "accepted",
                    "merged",
                    "uncertain",
                }:
                    self._apply_llm_arbitration_verdict(
                        blackboard,
                        conflict,
                        verdict,
                        decisions,
                    )
                    continue
            policy = (
                "claim_forbidden"
                if conflict.severity == ConflictSeverity.CRITICAL
                else "mark_unconfirmed"
            )
            decision = (
                "claim_forbidden"
                if conflict.severity == ConflictSeverity.CRITICAL
                else "reject_all"
            )
            rejected_claims = self._mark_conflict_facts(
                blackboard,
                conflict.answer_ids,
            )
            decisions.append(
                ArbitrationDecision(
                    conflict_id=conflict.conflict_id,
                    decision=decision,
                    rejected_answer_ids=conflict.answer_ids,
                    basis=conflict.description,
                    required_summary_policy=policy,
                ),
            )
            blackboard.do_not_claim_list.extend(rejected_claims)
        return decisions

    @staticmethod
    def _apply_llm_arbitration_verdict(
        blackboard: BlackboardState,
        conflict: Conflict,
        verdict: ArbitrationLLMVerdict,
        decisions: list[ArbitrationDecision],
    ) -> None:
        """Apply a structured arbitration verdict to the blackboard."""

        verdict_typed = verdict
        rejected = list(verdict_typed.rejected_answer_ids)
        if verdict_typed.outcome == "do_not_claim":
            rejected_claims = ArbitrationPanel._mark_conflict_facts(
                blackboard,
                conflict.answer_ids,
            )
            blackboard.do_not_claim_list.extend(rejected_claims)
            decisions.append(
                ArbitrationDecision(
                    conflict_id=conflict.conflict_id,
                    decision="claim_forbidden",
                    rejected_answer_ids=conflict.answer_ids,
                    basis=verdict_typed.basis or conflict.description,
                    required_summary_policy="claim_forbidden",
                ),
            )
            return
        if verdict_typed.outcome == "uncertain":
            rejected_claims = ArbitrationPanel._mark_conflict_facts(
                blackboard,
                conflict.answer_ids,
            )
            blackboard.do_not_claim_list.extend(rejected_claims)
            decisions.append(
                ArbitrationDecision(
                    conflict_id=conflict.conflict_id,
                    decision="reject_all",
                    rejected_answer_ids=conflict.answer_ids,
                    basis=verdict_typed.basis or conflict.description,
                    required_summary_policy="mark_unconfirmed",
                ),
            )
            return
        rejected_set = set(rejected)
        for fact in blackboard.facts:
            if fact.answer_id in rejected_set:
                fact.do_not_claim = True
                blackboard.do_not_claim_list.append(fact.claim)
        accepted_head = (
            verdict_typed.accepted_answer_ids[0]
            if verdict_typed.accepted_answer_ids
            else None
        )
        arb_decision = (
            "merge_claims" if verdict_typed.outcome == "merged" else "accept_claim"
        )
        decisions.append(
            ArbitrationDecision(
                conflict_id=conflict.conflict_id,
                decision=arb_decision,
                accepted_answer_id=accepted_head,
                rejected_answer_ids=rejected,
                basis=verdict_typed.basis or conflict.description,
                required_summary_policy="claim_allowed",
            ),
        )

    @staticmethod
    def _mark_conflict_facts(
        blackboard: BlackboardState,
        answer_ids: list[str],
    ) -> list[str]:
        """Mark facts involved in a conflict as unavailable for composition."""
        rejected_ids = set(answer_ids)
        rejected_claims: list[str] = []
        for fact in blackboard.facts:
            if fact.answer_id not in rejected_ids:
                continue
            fact.do_not_claim = True
            rejected_claims.append(fact.claim)
        return rejected_claims


class SummaryComposerAgent:
    """Compose a supported French summary draft from blackboard facts."""

    def __init__(
        self,
        llm_runner: object | None = None,
        config: JsonObject | None = None,
    ) -> None:
        """Initialize the composer."""
        self.llm_runner = llm_runner
        self.config = config or {}
        self.last_llm_usage = LLMUsageDelta()

    def _agentic_backend_enabled(self) -> bool:
        """Return True when the composer should call the LLM."""
        backend = str(self.config.get("backend", "deterministic"))
        if self.llm_runner is None:
            return False
        if backend == "api":
            return True
        if backend == "cursor_cli":
            return bool(self.config.get("cursor_cli_probe"))
        return False

    def compose(
        self,
        blackboard: BlackboardState,
        decisions: list[ArbitrationDecision],
        scene_timeline: object | None = None,
    ) -> SummaryDraft:
        """Create a summary draft without re-reading the transcription."""
        self.last_llm_usage = LLMUsageDelta()
        usable = [
            fact
            for fact in blackboard.facts
            if fact.status == FactStatus.SUPPORTED and not fact.do_not_claim
        ]
        if self._agentic_backend_enabled() and usable:
            runner = cast(LLMRunner, self.llm_runner)
            payload = [fact.model_dump(mode="json") for fact in usable]
            draft, usage = run_composer_llm(
                runner,
                facts_payload=payload,
                do_not_claim=list(blackboard.do_not_claim_list),
                scene_timeline=scene_timeline,
                context_text=_config_text(self.config, "context_text"),
                prior_context_text=_config_text(self.config, "prior_context_text"),
            )
            self.last_llm_usage = usage
            return draft.model_copy(
                update={
                    "metadata": {
                        "arbitration_decision_count": len(decisions),
                        "source": "llm_composer",
                    },
                },
            )
        executive = self._section(
            section_id="executive_summary",
            title="Résumé exécutif",
            facts=usable[:3],
        )
        key_points = self._section(
            section_id="key_points",
            title="Points clés",
            facts=usable[:8],
        )
        sections = [section for section in [executive, key_points] if section]
        markdown = "\n\n".join(
            f"## {section.title}\n{section.content}" for section in sections
        )
        return SummaryDraft(
            markdown=markdown,
            sections=sections,
            forbidden_claim_ids=list(blackboard.do_not_claim_list),
            metadata={"arbitration_decision_count": len(decisions)},
        )

    @staticmethod
    def _section(
        section_id: str,
        title: str,
        facts: list[BlackboardFact],
    ) -> SummarySection | None:
        """Build a supported summary section from facts."""
        supported_ids = [fact.answer_id for fact in facts if fact.answer_id is not None]
        if not supported_ids:
            return None
        bullets = "\n".join(
            f"- {_strip_agent_debug_prefix(fact.claim)}" for fact in facts
        )
        return SummarySection(
            section_id=section_id,
            title=title,
            content=bullets,
            supporting_answer_ids=supported_ids,
        )


class AdversarialAuditAgent:
    """Audit a summary draft for unsupported or forbidden claims."""

    def __init__(
        self,
        llm_runner: object | None = None,
        config: JsonObject | None = None,
    ) -> None:
        """Initialize the audit agent."""
        self.llm_runner = llm_runner
        self.config = config or {}
        self.last_llm_usage = LLMUsageDelta()

    def _agentic_backend_enabled(self) -> bool:
        """Return True when a semantic audit LLM pass should run."""
        backend = str(self.config.get("backend", "deterministic"))
        if self.llm_runner is None:
            return False
        if backend == "api":
            return True
        if backend == "cursor_cli":
            return bool(self.config.get("cursor_cli_probe"))
        return False

    def audit(
        self,
        draft: SummaryDraft,
        blackboard: BlackboardState,
        scene_timeline: object | None = None,
    ) -> list[AuditFinding]:
        """Audit a summary draft.

        Args:
            draft: Summary draft.
            blackboard: Blackboard state.

        Returns:
            Audit findings.
        """
        self.last_llm_usage = LLMUsageDelta()
        findings: list[AuditFinding] = []
        allowed_ids = {
            fact.answer_id
            for fact in blackboard.facts
            if fact.answer_id
            and fact.status == FactStatus.SUPPORTED
            and not fact.do_not_claim
        }
        forbidden = set(blackboard.do_not_claim_list)
        for section in draft.sections:
            unsupported = [
                answer_id
                for answer_id in section.supporting_answer_ids
                if answer_id not in allowed_ids
            ]
            if unsupported:
                findings.append(
                    AuditFinding(
                        finding_id=f"audit_unsupported_{section.section_id}",
                        severity=ConflictSeverity.CRITICAL,
                        claim=section.content,
                        issue="Section references unsupported answer IDs.",
                        required_action="mark_unconfirmed",
                        related_answer_ids=unsupported,
                    ),
                )
            leaks = [claim for claim in forbidden if claim and claim in section.content]
            if leaks:
                findings.append(
                    AuditFinding(
                        finding_id=f"audit_forbidden_{section.section_id}",
                        severity=ConflictSeverity.CRITICAL,
                        claim=section.content,
                        issue="Section leaks a forbidden claim.",
                        required_action="remove",
                        related_answer_ids=section.supporting_answer_ids,
                    ),
                )
        if not draft.sections:
            findings.append(
                AuditFinding(
                    finding_id="audit_empty_draft",
                    severity=ConflictSeverity.CRITICAL,
                    claim="empty_draft",
                    issue="No supported summary sections were produced.",
                    required_action="replan",
                ),
            )
        if self._agentic_backend_enabled():
            runner = cast(LLMRunner, self.llm_runner)
            issues, usage = run_audit_llm(
                runner,
                draft_markdown=draft.markdown,
                facts_payload=blackboard_facts_to_payload(blackboard),
                do_not_claim=list(blackboard.do_not_claim_list),
                scene_timeline=scene_timeline,
                context_text=_config_text(self.config, "context_text"),
            )
            self.last_llm_usage = usage
            for index, issue in enumerate(issues):
                findings.append(
                    AuditFinding(
                        finding_id=f"audit_llm_{index:03d}",
                        severity=ConflictSeverity.CRITICAL,
                        claim=issue,
                        issue=issue,
                        required_action="rewrite",
                        related_answer_ids=[],
                    ),
                )
        return findings


class FinalPatchAgent:
    """Apply audit findings without adding new facts."""

    def patch(
        self,
        draft: SummaryDraft,
        findings: list[AuditFinding],
    ) -> FinalSummary:
        """Create a final summary from a draft and audit findings."""
        critical_findings = [
            finding
            for finding in findings
            if finding.severity == ConflictSeverity.CRITICAL
        ]
        warnings = [finding.issue for finding in critical_findings]
        markdown = draft.markdown
        return FinalSummary(
            markdown=markdown,
            sections=draft.sections,
            findings=findings,
            warnings=warnings,
            metadata={"patched": bool(findings)},
        )


class CharacterAttributionVerifierAgent:
    """Correct fragile character attributions in the final summary."""

    def verify(
        self,
        final: FinalSummary,
        blackboard: BlackboardState,
    ) -> FinalSummary:
        """Neutralize actor names for events marked ambiguous by the blackboard."""
        rules = _ambiguous_actor_verification_rules(blackboard)
        if not rules:
            return final
        markdown, markdown_count = _apply_actor_verification_rules(
            final.markdown,
            rules,
        )
        sections: list[SummarySection] = []
        section_count = 0
        for section in final.sections:
            content, count = _apply_actor_verification_rules(section.content, rules)
            section_count += count
            sections.append(section.model_copy(update={"content": content}))
        correction_count = markdown_count + section_count
        if correction_count <= 0:
            return final
        return final.model_copy(
            update={
                "markdown": markdown,
                "sections": sections,
                "metadata": {
                    **final.metadata,
                    "character_attribution_verified": True,
                    "character_attribution_correction_count": correction_count,
                },
            },
        )


class AnalysisOrchestrator:
    """Run the bounded blackboard analysis loop."""

    def __init__(
        self,
        max_audit_attempts: int = 3,
        llm_runner: object | None = None,
        specialist_config: JsonObject | None = None,
    ) -> None:
        """Initialize the orchestrator."""
        if max_audit_attempts < 1:
            raise ValueError("max_audit_attempts must be at least one.")
        self._max_audit_attempts = max_audit_attempts
        self._specialist_config = specialist_config or {}
        self._specialist_backend = str(
            self._specialist_config.get("backend", "deterministic")
        )
        self._cursor_cli_probe = bool(self._specialist_config.get("cursor_cli_probe"))
        self._planner = AnalysisPlannerAgent()
        self._specialists = _default_specialists(
            llm_runner=llm_runner,
            config=specialist_config,
        )
        self._blackboard = BlackboardController()
        self._arbitration = ArbitrationPanel()
        self._composer = SummaryComposerAgent(
            llm_runner=llm_runner,
            config=specialist_config,
        )
        self._audit = AdversarialAuditAgent(
            llm_runner=llm_runner,
            config=specialist_config,
        )
        self._patch = FinalPatchAgent()
        self._character_verifier = CharacterAttributionVerifierAgent()

    def run(
        self,
        retriever: EvidenceRetriever,
        *,
        scene_timeline: object | None = None,
        initial_answers: list[EvidenceAnswer] | None = None,
    ) -> PipelineResult:
        """Run the bounded analysis loop over a retriever."""
        audit_feedback: list[AuditFinding] = []
        seed_answers = list(initial_answers or [])
        last_result: PipelineResult | None = None
        llm_runner_obj = self._specialists["ChronologyAgent"].llm_runner
        llm_runner_typed = cast(LLMRunner, llm_runner_obj) if llm_runner_obj else None
        for attempt in range(1, self._max_audit_attempts + 1):
            plan = self._planner.plan(audit_feedback)
            answers, specialist_usage = _run_specialists_with_usage(
                plan,
                retriever,
                self._specialists,
            )
            answers = [*seed_answers, *answers]
            blackboard = self._blackboard.ingest(answers)
            decisions = self._arbitration.arbitrate(
                blackboard,
                llm_runner=llm_runner_typed,
                specialist_backend=self._specialist_backend,
                cursor_cli_probe=self._cursor_cli_probe,
                retriever=retriever,
                context_text=_config_text(self._specialist_config, "context_text"),
            )
            arbitration_usage = self._arbitration.last_llm_usage
            draft = self._composer.compose(
                blackboard,
                decisions,
                scene_timeline=scene_timeline,
            )
            composition_usage = self._composer.last_llm_usage
            findings = self._audit.audit(
                draft,
                blackboard,
                scene_timeline=scene_timeline,
            )
            audit_usage = self._audit.last_llm_usage
            final = self._patch.patch(draft, findings)
            final = self._character_verifier.verify(final, blackboard)
            analysis_calls = specialist_usage.calls + arbitration_usage.calls
            total_tokens = (
                specialist_usage.tokens
                + arbitration_usage.tokens
                + composition_usage.tokens
                + audit_usage.tokens
            )
            total_cost = (
                specialist_usage.cost_usd
                + arbitration_usage.cost_usd
                + composition_usage.cost_usd
                + audit_usage.cost_usd
            )
            merged_cost = (final.estimated_cost_usd or 0.0) + total_cost
            final = final.model_copy(
                update={
                    "analysis_llm_call_count": analysis_calls,
                    "composition_llm_call_count": composition_usage.calls,
                    "audit_llm_call_count": audit_usage.calls,
                    "llm_call_count": analysis_calls
                    + composition_usage.calls
                    + audit_usage.calls,
                    "estimated_llm_tokens": final.estimated_llm_tokens + total_tokens,
                    "estimated_cost_usd": merged_cost if merged_cost > 0 else None,
                },
            )
            last_result = PipelineResult(
                plan=plan,
                answers=answers,
                blackboard=blackboard,
                decisions=decisions,
                draft=draft,
                findings=findings,
                final_summary=final,
                attempts=attempt,
            )
            if not any(finding.required_action == "replan" for finding in findings):
                return last_result
            audit_feedback = findings
        if last_result is None:
            raise RuntimeError("Analysis loop did not produce a result.")
        return last_result


def _question(
    question_id: str,
    agent: str,
    claim_type: ClaimType,
    queries: list[str],
    priority: int,
    risk_level: ConflictSeverity = ConflictSeverity.MINOR,
) -> AnalysisQuestion:
    """Create one standard planning question."""
    return AnalysisQuestion(
        question_id=question_id,
        priority=priority,
        retrieval_queries=[
            RetrievalQuery(
                query_id=f"{question_id}_{index:02d}",
                text=query,
                question_id=question_id,
            )
            for index, query in enumerate(queries)
        ],
        required_output_schema={"claim_type": claim_type.value},
        risk_level=risk_level,
        responsible_agent=agent,
    )


def _default_specialists(
    llm_runner: object | None = None,
    config: JsonObject | None = None,
) -> dict[str, SpecialistAgent]:
    """Return the standard specialist registry."""
    return {
        "ChronologyAgent": ChronologyAgent(llm_runner=llm_runner, config=config),
        "CombatOutcomeAgent": CombatOutcomeAgent(llm_runner=llm_runner, config=config),
        "CharacterStateAgent": CharacterStateAgent(
            llm_runner=llm_runner,
            config=config,
        ),
        "QuestContinuityAgent": QuestContinuityAgent(
            llm_runner=llm_runner,
            config=config,
        ),
        "UncertaintyAgent": UncertaintyAgent(llm_runner=llm_runner, config=config),
    }


def _run_specialists_with_usage(
    plan: AnalysisPlan,
    retriever: EvidenceRetriever,
    specialists: dict[str, SpecialistAgent],
) -> tuple[list[EvidenceAnswer], LLMUsageDelta]:
    """Run all planned specialist questions and accumulate LLM usage."""
    answers: list[EvidenceAnswer] = []
    usage = LLMUsageDelta()
    for question in plan.questions:
        specialist = specialists[question.responsible_agent]
        answers.extend(specialist.answer(question, retriever))
        usage = _merge_usage_delta(usage, specialist.last_llm_usage)
    return answers, usage


def _question_claim_type(
    question: AnalysisQuestion,
    fallback: ClaimType,
) -> ClaimType:
    """Return the claim type requested by a planned question."""
    raw_claim_type = question.required_output_schema.get("claim_type")
    if isinstance(raw_claim_type, str):
        return ClaimType(raw_claim_type)
    return fallback


def _config_text(config: JsonObject, key: str) -> str | None:
    """Return a non-empty string value from agent configuration."""
    value = config.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _dedupe_results(results: list[RetrievedEvidence]) -> list[RetrievedEvidence]:
    """Deduplicate retrieved evidence by chunk ID while preserving order."""
    seen: set[str] = set()
    deduped: list[RetrievedEvidence] = []
    for result in sorted(results, key=lambda item: item.score, reverse=True):
        if result.chunk.chunk_id in seen:
            continue
        seen.add(result.chunk.chunk_id)
        deduped.append(result)
    return deduped


def _short_text(text: str, limit: int = 180) -> str:
    """Return a compact one-line claim excerpt."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _ambiguous_actor_verification_rules(
    blackboard: BlackboardState,
) -> dict[str, set[str]]:
    """Build event-key to actor-name rules for final summary verification."""
    neutral_event_keys = {
        key
        for fact in blackboard.facts
        if fact.status == FactStatus.SUPPORTED
        and not fact.do_not_claim
        and (key := _actor_event_key(fact.claim)) is not None
        and _has_neutral_actor(fact.claim)
    }
    rules: dict[str, set[str]] = {}
    forbidden_claims = list(blackboard.do_not_claim_list)
    forbidden_claims.extend(
        fact.claim
        for fact in blackboard.facts
        if fact.do_not_claim and fact.status == FactStatus.SUPPORTED
    )
    for claim in forbidden_claims:
        event_key = _actor_event_key(claim)
        if event_key is None or event_key not in neutral_event_keys:
            continue
        names = _actor_names_from_claim(claim)
        if names:
            rules.setdefault(event_key, set()).update(names)
    return rules


def _apply_actor_verification_rules(
    text: str,
    rules: dict[str, set[str]],
) -> tuple[str, int]:
    """Apply actor-neutralization rules to summary text."""
    if not text:
        return text, 0
    replacement = text
    total = 0
    for event_key, names in rules.items():
        for name in sorted(names, key=len, reverse=True):
            replacement, count = _neutralize_actor_in_event_clauses(
                replacement,
                event_key,
                name,
            )
            total += count
    return replacement, total


def _neutralize_actor_in_event_clauses(
    text: str,
    event_key: str,
    actor_name: str,
) -> tuple[str, int]:
    """Replace one actor name with neutral wording in matching clauses."""
    if not actor_name:
        return text, 0
    pattern = re.compile(
        rf"(?P<clause>[^.;\n]*\b{re.escape(actor_name)}\b[^.;\n]*)",
        flags=re.IGNORECASE,
    )
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        clause = match.group("clause")
        if not _clause_mentions_actor_event(clause, event_key):
            return clause
        count += 1
        neutral = _neutral_actor_label(clause)
        return re.sub(
            rf"\b{re.escape(actor_name)}\b",
            neutral,
            clause,
            count=1,
            flags=re.IGNORECASE,
        )

    return pattern.sub(replace, text), count


def _clause_mentions_actor_event(clause: str, event_key: str) -> bool:
    """Return True when a clause describes the ambiguous event kind."""
    text = _normalize_claim(clause)
    event_terms = {
        "poisoned_projectile": (
            "arbalete",
            "arrow",
            "bolt",
            "carreau",
            "crossbow",
            "empoison",
            "poison",
            "tir",
            "touch",
        ),
        "projectile_hit": (
            "arbalete",
            "arrow",
            "bolt",
            "carreau",
            "crossbow",
            "frappe",
            "hit",
            "shot",
            "tir",
            "touch",
        ),
        "poison_condition": (
            "cleared",
            "condition",
            "dissip",
            "empoison",
            "no longer",
            "poison",
            "removed",
        ),
        "river_crossing_fall": (
            "chute",
            "eau",
            "emport",
            "fall",
            "fleuve",
            "river",
            "tomb",
            "water",
        ),
    }
    terms = event_terms.get(event_key, ())
    return any(term in text for term in terms)


def _neutral_actor_label(clause: str) -> str:
    """Choose a French neutral actor label that fits the local wording."""
    text = _normalize_claim(clause)
    if "groupe" in text:
        return "un membre du groupe"
    return "un compagnon"


def _actor_names_from_claim(claim: str) -> set[str]:
    """Extract likely actor names from a forbidden actor-specific claim."""
    blocked = {
        "ASR",
        "At",
        "Constitution",
        "During",
        "From",
        "Misty",
        "Mystic",
        "The",
        "While",
    }
    names = set(re.findall(r"\b[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'ùûîïéèêëàâäôöç-]{2,}\b", claim))
    return {name for name in names if name not in blocked}


def _ambiguous_actor_event_keys(facts: list[BlackboardFact]) -> set[str]:
    """Return event keys that have both neutral and actor-specific variants."""
    has_neutral: set[str] = set()
    has_named_actor: set[str] = set()
    for fact in facts:
        if fact.status != FactStatus.SUPPORTED or fact.do_not_claim:
            continue
        event_key = _actor_event_key(fact.claim)
        if event_key is None:
            continue
        if _has_neutral_actor(fact.claim):
            has_neutral.add(event_key)
        else:
            has_named_actor.add(event_key)
    return has_neutral & has_named_actor


def _actor_event_key(claim: str) -> str | None:
    """Classify claims where actor confusion is safer to neutralize."""
    text = _normalize_claim(claim)
    projectile_terms = (
        "arrow",
        "bolt",
        "carreau",
        "crossbow",
        "projectile",
        "shot",
        "shoot",
        "tir",
    )
    poison_terms = ("poison", "poisoned", "empoison")
    if any(term in text for term in projectile_terms) and any(
        term in text for term in poison_terms
    ):
        return "poisoned_projectile"
    hit_terms = ("hit", "struck", "touch", "frappe")
    if any(term in text for term in projectile_terms) and any(
        term in text for term in hit_terms
    ):
        return "projectile_hit"

    poison_state_terms = (
        "cleared",
        "condition",
        "gained",
        "gains",
        "later loses",
        "lost",
        "no longer",
        "removed",
    )
    if any(term in text for term in poison_terms) and any(
        term in text for term in poison_state_terms
    ):
        return "poison_condition"

    water_terms = ("eau", "fleuve", "river", "water")
    fall_terms = ("carried", "emport", "fall", "fell", "swept", "tomb")
    crossing_terms = ("bridge", "crossing", "passage", "tronc", "travers")
    if (
        any(term in text for term in water_terms)
        and any(term in text for term in fall_terms)
        and any(term in text for term in crossing_terms)
    ):
        return "river_crossing_fall"
    return None


def _has_neutral_actor(claim: str) -> bool:
    """Return True when a claim intentionally avoids naming the actor."""
    text = _normalize_claim(claim)
    neutral_terms = (
        "a character",
        "a companion",
        "a party member",
        "a party-member",
        "a group member",
        "at least one party member",
        "quelqu'un",
        "the group",
        "un compagnon",
        "un membre",
        "un personnage",
    )
    return any(term in text for term in neutral_terms)


def _normalize_claim(claim: str) -> str:
    """Normalize a claim for exact deduplication."""
    return " ".join(claim.casefold().split())


def _strip_agent_debug_prefix(claim: str) -> str:
    """Remove internal specialist prefixes such as ``ChronologyAgent:``."""
    if ":" not in claim:
        return claim
    head, tail = claim.split(":", 1)
    if head.strip().endswith("Agent"):
        return tail.strip()
    return claim


def _merge_usage_delta(left: LLMUsageDelta, right: LLMUsageDelta) -> LLMUsageDelta:
    """Sum two usage deltas."""
    return LLMUsageDelta(
        calls=left.calls + right.calls,
        tokens=left.tokens + right.tokens,
        cost_usd=left.cost_usd + right.cost_usd,
    )


def _critical_claims_are_complementary(claims: list[str]) -> bool:
    """Return True when competing claims look like complementary details."""
    if len(claims) < 2:
        return True
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            if not _claims_pair_complementary(claims[i], claims[j]):
                return False
    return True


def _claims_pair_complementary(left: str, right: str) -> bool:
    """Heuristic: overlapping topical detail without hard contradiction."""
    a, b = _normalize_claim(left), _normalize_claim(right)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    tokens_a = {tok.strip(".,;:!?") for tok in a.split() if tok}
    tokens_b = {tok.strip(".,;:!?") for tok in b.split() if tok}
    overlap = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(overlap) / len(union) if union else 0.0
    if jaccard >= 0.45:
        return True
    death_tokens = {"mort", "meurt", "tué", "tue", "dead"}
    life_tokens = {
        "vivant",
        "survit",
        "surviv",
        "debout",
        "stable",
        "inconscient",
        "stabilise",
        "stabilisé",
    }
    a_death = bool(tokens_a & death_tokens)
    b_death = bool(tokens_b & death_tokens)
    a_life = bool(tokens_a & life_tokens)
    b_life = bool(tokens_b & life_tokens)
    if (a_death and b_life) or (b_death and a_life):
        return False
    return jaccard >= 0.2


def _conflict_fact_rows(
    conflict: Conflict,
    blackboard: BlackboardState,
    retriever: EvidenceRetriever | None,
) -> list[dict[str, Any]]:
    """Serialize facts in a conflict for arbitration prompts."""
    rows: list[dict[str, Any]] = []
    for fact in blackboard.facts:
        if fact.answer_id not in set(conflict.answer_ids):
            continue
        snippet = ""
        getter = getattr(retriever, "get_chunk", None)
        if callable(getter):
            snippets: list[str] = []
            for support in fact.support[:3]:
                chunk = getter(support.chunk_id)
                if chunk is not None and hasattr(chunk, "text"):
                    snippets.append(str(chunk.text)[:400])
            snippet = " | ".join(snippets)
        rows.append(
            {
                "answer_id": fact.answer_id,
                "claim": fact.claim,
                "evidence_snippet": snippet,
            },
        )
    return rows
