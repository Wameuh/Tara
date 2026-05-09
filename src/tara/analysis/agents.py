"""Deterministic agent layer for the Tara blackboard analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

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
                ["chronologie evenement majeur", "mort sanctuaire temple"],
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
                ["etat final personnage position ressource", "potion soin blessure"],
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
    """Base deterministic specialist that answers from retrieved chunks only."""

    agent_name: str = "SpecialistAgent"
    claim_type: ClaimType = ClaimType.CHRONOLOGY

    def __init__(
        self,
        llm_runner: object | None = None,
        config: JsonObject | None = None,
    ) -> None:
        """Initialize a specialist agent.

        Args:
            llm_runner: Optional future LLM runner injection point.
            config: Optional JSON-compatible agent configuration.
        """
        self.llm_runner = llm_runner
        self.config = config or {}

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
        return [
            self._answer_from_evidence(question, result, index)
            for index, result in enumerate(deduped)
        ]

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
            claim=f"{self.agent_name}: {_short_text(chunk.text)}",
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
                }
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
        conflicts = self._detect_conflicts(facts)
        return BlackboardState(
            facts=facts,
            conflicts=conflicts,
            do_not_claim_list=do_not_claim,
        )

    @staticmethod
    def _detect_conflicts(facts: list[BlackboardFact]) -> list[Conflict]:
        """Detect simple competing critical claims by claim type."""
        conflicts: list[Conflict] = []
        grouped: dict[ClaimType, list[BlackboardFact]] = {}
        for fact in facts:
            if fact.claim_type is None or fact.status != FactStatus.SUPPORTED:
                continue
            grouped.setdefault(fact.claim_type, []).append(fact)
        for claim_type, candidates in grouped.items():
            critical = [fact for fact in candidates if fact.importance >= 4]
            claims = {_normalize_claim(fact.claim) for fact in critical}
            if len(critical) > 1 and len(claims) > 1:
                severity = (
                    ConflictSeverity.CRITICAL
                    if any(
                        fact.is_critical or fact.importance >= 5
                        for fact in critical
                    )
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
                    )
                )
        return conflicts


class ArbitrationPanel:
    """Apply deterministic arbitration policies before composition."""

    def arbitrate(self, blackboard: BlackboardState) -> list[ArbitrationDecision]:
        """Resolve or quarantine conflicts.

        Args:
            blackboard: Current blackboard state.

        Returns:
            Arbitration decisions.
        """
        decisions: list[ArbitrationDecision] = []
        for conflict in blackboard.conflicts:
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
                )
            )
            blackboard.do_not_claim_list.extend(rejected_claims)
        return decisions

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

    def compose(
        self,
        blackboard: BlackboardState,
        decisions: list[ArbitrationDecision],
    ) -> SummaryDraft:
        """Create a summary draft without re-reading the transcription."""
        usable = [
            fact
            for fact in blackboard.facts
            if fact.status == FactStatus.SUPPORTED and not fact.do_not_claim
        ]
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
        supported_ids = [
            fact.answer_id for fact in facts if fact.answer_id is not None
        ]
        if not supported_ids:
            return None
        bullets = "\n".join(f"- {fact.claim}" for fact in facts)
        return SummarySection(
            section_id=section_id,
            title=title,
            content=bullets,
            supporting_answer_ids=supported_ids,
        )


class AdversarialAuditAgent:
    """Audit a summary draft for unsupported or forbidden claims."""

    def audit(
        self,
        draft: SummaryDraft,
        blackboard: BlackboardState,
    ) -> list[AuditFinding]:
        """Audit a summary draft.

        Args:
            draft: Summary draft.
            blackboard: Blackboard state.

        Returns:
            Audit findings.
        """
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
                    )
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
                    )
                )
        if not draft.sections:
            findings.append(
                AuditFinding(
                    finding_id="audit_empty_draft",
                    severity=ConflictSeverity.CRITICAL,
                    claim="empty_draft",
                    issue="No supported summary sections were produced.",
                    required_action="replan",
                )
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
        if critical_findings:
            markdown += "\n\n## Non-confirmed\n"
            markdown += "\n".join(f"- {finding.claim}" for finding in critical_findings)
        return FinalSummary(
            markdown=markdown,
            sections=draft.sections,
            findings=findings,
            warnings=warnings,
            metadata={"patched": bool(findings)},
        )


class AnalysisOrchestrator:
    """Run the bounded blackboard analysis loop."""

    def __init__(self, max_audit_attempts: int = 3) -> None:
        """Initialize the orchestrator."""
        if max_audit_attempts < 1:
            raise ValueError("max_audit_attempts must be at least one.")
        self._max_audit_attempts = max_audit_attempts
        self._planner = AnalysisPlannerAgent()
        self._specialists = _default_specialists()
        self._blackboard = BlackboardController()
        self._arbitration = ArbitrationPanel()
        self._composer = SummaryComposerAgent()
        self._audit = AdversarialAuditAgent()
        self._patch = FinalPatchAgent()

    def run(self, retriever: EvidenceRetriever) -> PipelineResult:
        """Run the bounded analysis loop over a retriever."""
        audit_feedback: list[AuditFinding] = []
        last_result: PipelineResult | None = None
        for attempt in range(1, self._max_audit_attempts + 1):
            plan = self._planner.plan(audit_feedback)
            answers = _run_specialists(plan, retriever, self._specialists)
            blackboard = self._blackboard.ingest(answers)
            decisions = self._arbitration.arbitrate(blackboard)
            draft = self._composer.compose(blackboard, decisions)
            findings = self._audit.audit(draft, blackboard)
            final = self._patch.patch(draft, findings)
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


def _default_specialists() -> dict[str, SpecialistAgent]:
    """Return the standard specialist registry."""
    return {
        "ChronologyAgent": ChronologyAgent(),
        "CombatOutcomeAgent": CombatOutcomeAgent(),
        "CharacterStateAgent": CharacterStateAgent(),
        "QuestContinuityAgent": QuestContinuityAgent(),
        "UncertaintyAgent": UncertaintyAgent(),
    }


def _run_specialists(
    plan: AnalysisPlan,
    retriever: EvidenceRetriever,
    specialists: dict[str, SpecialistAgent],
) -> list[EvidenceAnswer]:
    """Run all planned specialist questions."""
    answers: list[EvidenceAnswer] = []
    for question in plan.questions:
        specialist = specialists[question.responsible_agent]
        answers.extend(specialist.answer(question, retriever))
    return answers


def _question_claim_type(
    question: AnalysisQuestion,
    fallback: ClaimType,
) -> ClaimType:
    """Return the claim type requested by a planned question."""
    raw_claim_type = question.required_output_schema.get("claim_type")
    if isinstance(raw_claim_type, str):
        return ClaimType(raw_claim_type)
    return fallback


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


def _normalize_claim(claim: str) -> str:
    """Normalize a claim for exact deduplication."""
    return " ".join(claim.casefold().split())
