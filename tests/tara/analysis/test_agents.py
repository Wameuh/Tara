"""Tests for deterministic blackboard analysis agents."""

from __future__ import annotations

from tara.analysis.agents import (
    AdversarialAuditAgent,
    AnalysisOrchestrator,
    AnalysisPlannerAgent,
    ArbitrationPanel,
    BlackboardController,
    CharacterAttributionVerifierAgent,
    ChronologyAgent,
    FinalPatchAgent,
    QuestContinuityAgent,
    SummaryComposerAgent,
    UncertaintyAgent,
)
from tara.analysis.evidence_index import EvidenceIndex
from tara.analysis.models import (
    AuditFinding,
    BlackboardFact,
    ClaimType,
    Confidence,
    ConflictSeverity,
    EvidenceAnswer,
    EvidenceSupport,
    FactStatus,
    FinalSummary,
    SummaryDraft,
    SummarySection,
)
from tara.schemas.merged_transcription import new_merged_transcription


def _index() -> EvidenceIndex:
    """Create a synthetic evidence index for agent tests."""
    transcription = new_merged_transcription(
        text=(
            "Molnir est mort dans le sanctuaire. "
            "Aelia utilise une potion. "
            "La lance ouvre le mécanisme du temple."
        ),
        segments=[
            {
                "start": 0.0,
                "end": 30.0,
                "text": "Molnir est mort dans le sanctuaire.",
            },
            {
                "start": 30.0,
                "end": 60.0,
                "text": "Aelia utilise une potion.",
            },
            {
                "start": 60.0,
                "end": 90.0,
                "text": "La lance ouvre le mécanisme du temple.",
            },
        ],
        duration=90.0,
    )
    return EvidenceIndex.from_transcription(
        transcription,
        target_window_seconds=45.0,
        overlap_seconds=15.0,
    )


def _support() -> EvidenceSupport:
    """Create a reusable support reference."""
    return EvidenceSupport(chunk_id="chunk_0000", start=0.0, end=30.0, segment_ids=[0])


def test_planner_creates_standard_questions() -> None:
    """The deterministic planner should cover the standard specialist set."""
    plan = AnalysisPlannerAgent().plan()

    assert {question.question_id for question in plan.questions} >= {
        "chronology",
        "combat_outcome",
        "character_state",
        "quest_continuity",
        "resource_state",
        "uncertainty",
    }
    assert all(question.retrieval_queries for question in plan.questions)
    assert all(question.responsible_agent for question in plan.questions)


def test_specialist_answers_only_from_retrieved_chunks() -> None:
    """Specialists should emit supported answers with chunk references."""
    question = AnalysisPlannerAgent().plan().questions[0]
    answers = ChronologyAgent().answer(question, _index())

    assert answers
    assert all(answer.status == FactStatus.SUPPORTED for answer in answers)
    assert all(answer.support for answer in answers)
    assert "Molnir" in answers[0].claim or "sanctuaire" in answers[0].claim.casefold()


def test_specialist_fallback_is_uncertain_without_evidence() -> None:
    """Empty retrieval should produce uncertainty instead of hallucination."""
    question = AnalysisPlannerAgent().plan().questions[0]
    empty_index = EvidenceIndex.from_transcription(
        new_merged_transcription(text="", segments=[]),
    )

    answers = ChronologyAgent().answer(question, empty_index)

    assert answers[0].status == FactStatus.UNCERTAIN
    assert not answers[0].support


def test_blackboard_ingests_and_marks_rejected_claims() -> None:
    """Blackboard should keep rejected facts but add them to do-not-claim."""
    answer = EvidenceAnswer(
        answer_id="a001",
        question_id="q001",
        claim="Ne pas affirmer que Molnir survit.",
        status=FactStatus.REJECTED,
        importance=3,
        confidence=Confidence.HIGH,
        claim_type=ClaimType.COMBAT_OUTCOME,
        support=[_support()],
    )

    state = BlackboardController().ingest([answer])

    assert state.facts[0].do_not_claim is True
    assert state.do_not_claim_list == [answer.claim]


def test_blackboard_detects_critical_conflicts() -> None:
    """Competing critical claims of the same type should become conflicts."""
    answers = [
        EvidenceAnswer(
            answer_id="a001",
            question_id="combat",
            claim="Molnir est mort.",
            status=FactStatus.SUPPORTED,
            importance=5,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="a002",
            question_id="combat",
            claim="Molnir survit.",
            status=FactStatus.SUPPORTED,
            importance=5,
            confidence=Confidence.MEDIUM,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
    ]

    state = BlackboardController().ingest(answers)

    assert state.conflicts
    assert state.conflicts[0].answer_ids == ["a001", "a002"]


def test_blackboard_prefers_neutral_actor_for_projectile_attribution() -> None:
    """Noisy actor variants should yield a neutral supported event."""
    answers = [
        EvidenceAnswer(
            answer_id="neutral",
            question_id="combat",
            claim=(
                "A character crossing the river was hit by a poisoned crossbow bolt."
            ),
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="kaknyr",
            question_id="combat",
            claim="Kaknyr was hit by a poisoned crossbow bolt.",
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="iluvatar",
            question_id="combat",
            claim="Ilùvatar was hit by a poisoned crossbow bolt.",
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="neutral_condition",
            question_id="state",
            claim="At least one party member later loses the poisoned condition.",
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.CHARACTER_STATE,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="neutral_hit",
            question_id="combat",
            claim="A party member was hit by a crossbow bolt from the far bank.",
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="iluvatar_hit",
            question_id="combat",
            claim="Ilùvatar was hit by a crossbow bolt from the far bank.",
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
            support=[_support()],
        ),
        EvidenceAnswer(
            answer_id="garath_condition",
            question_id="state",
            claim="Garath was no longer poisoned after the encounter.",
            status=FactStatus.SUPPORTED,
            importance=4,
            confidence=Confidence.MEDIUM,
            claim_type=ClaimType.CHARACTER_STATE,
            support=[_support()],
        ),
    ]

    state = BlackboardController().ingest(answers)
    facts_by_id = {fact.answer_id: fact for fact in state.facts}

    assert facts_by_id["neutral"].do_not_claim is False
    assert facts_by_id["kaknyr"].do_not_claim is True
    assert facts_by_id["iluvatar"].do_not_claim is True
    assert facts_by_id["neutral_condition"].do_not_claim is False
    assert facts_by_id["neutral_hit"].do_not_claim is False
    assert facts_by_id["iluvatar_hit"].do_not_claim is True
    assert facts_by_id["garath_condition"].do_not_claim is True
    assert "Kaknyr was hit by a poisoned crossbow bolt." in state.do_not_claim_list
    assert "Ilùvatar was hit by a poisoned crossbow bolt." in state.do_not_claim_list
    assert "Garath was no longer poisoned after the encounter." in (
        state.do_not_claim_list
    )


def test_arbitration_marks_critical_conflicts_forbidden() -> None:
    """Arbitration should stop conflicts from silently reaching composition."""
    state = BlackboardController().ingest(
        [
            EvidenceAnswer(
                answer_id="a001",
                question_id="combat",
                claim="Molnir est mort.",
                status=FactStatus.SUPPORTED,
                importance=5,
                confidence=Confidence.HIGH,
                claim_type=ClaimType.COMBAT_OUTCOME,
                support=[_support()],
            ),
            EvidenceAnswer(
                answer_id="a002",
                question_id="combat",
                claim="Molnir survit.",
                status=FactStatus.SUPPORTED,
                importance=5,
                confidence=Confidence.MEDIUM,
                claim_type=ClaimType.COMBAT_OUTCOME,
                support=[_support()],
            ),
        ]
    )

    decisions = ArbitrationPanel().arbitrate(state)

    assert decisions[0].decision == "claim_forbidden"
    assert decisions[0].required_summary_policy == "claim_forbidden"
    assert state.do_not_claim_list == ["Molnir est mort.", "Molnir survit."]
    assert all(fact.do_not_claim for fact in state.facts)


def test_composer_excludes_arbitrated_conflicts() -> None:
    """Facts quarantined by arbitration should not be composed."""
    state = BlackboardController().ingest(
        [
            EvidenceAnswer(
                answer_id="a001",
                question_id="combat",
                claim="Molnir est mort.",
                status=FactStatus.SUPPORTED,
                importance=5,
                confidence=Confidence.HIGH,
                claim_type=ClaimType.COMBAT_OUTCOME,
                support=[_support()],
            ),
            EvidenceAnswer(
                answer_id="a002",
                question_id="combat",
                claim="Molnir survit.",
                status=FactStatus.SUPPORTED,
                importance=5,
                confidence=Confidence.MEDIUM,
                claim_type=ClaimType.COMBAT_OUTCOME,
                support=[_support()],
            ),
        ]
    )
    decisions = ArbitrationPanel().arbitrate(state)

    draft = SummaryComposerAgent().compose(state, decisions)

    assert draft.sections == []
    assert "Molnir est mort." in draft.forbidden_claim_ids


def test_resource_question_uses_resource_claim_type() -> None:
    """QuestContinuityAgent should honor the planned resource claim type."""
    plan = AnalysisPlannerAgent().plan()
    question = next(
        item for item in plan.questions if item.question_id == "resource_state"
    )

    answers = QuestContinuityAgent().answer(question, _index())

    assert answers
    assert all(answer.claim_type == ClaimType.RESOURCE_STATE for answer in answers)


def test_uncertainty_agent_marks_forbidden_text_rejected() -> None:
    """UncertaintyAgent should reject explicitly forbidden evidence."""
    transcription = new_merged_transcription(
        text="Il est interdit d'affirmer que Molnir survit.",
        segments=[
            {
                "start": 0.0,
                "end": 10.0,
                "text": "Il est interdit d'affirmer que Molnir survit.",
            }
        ],
    )
    index = EvidenceIndex.from_transcription(transcription, 20.0, 0.0)
    question = next(
        item
        for item in AnalysisPlannerAgent().plan().questions
        if item.question_id == "uncertainty"
    )

    answers = UncertaintyAgent().answer(question, index)

    assert answers[0].status == FactStatus.REJECTED


def test_composer_uses_supported_blackboard_facts() -> None:
    """Composer should produce French sections with supporting IDs."""
    fact = BlackboardFact(
        fact_id="f001",
        answer_id="a001",
        claim="ChronologyAgent: Molnir est mort dans le sanctuaire.",
        status=FactStatus.SUPPORTED,
        confidence=Confidence.HIGH,
        importance=4,
        claim_type=ClaimType.CHRONOLOGY,
        support=[_support()],
    )

    draft = SummaryComposerAgent().compose(
        blackboard=BlackboardController().ingest(
            [
                EvidenceAnswer(
                    answer_id="a001",
                    question_id="chronology",
                    claim=fact.claim,
                    status=FactStatus.SUPPORTED,
                    importance=4,
                    confidence=Confidence.HIGH,
                    claim_type=ClaimType.CHRONOLOGY,
                    support=[_support()],
                )
            ]
        ),
        decisions=[],
    )

    assert "Résumé exécutif" in draft.markdown
    assert draft.sections[0].supporting_answer_ids == ["a001"]


def test_audit_detects_unsupported_section_references() -> None:
    """Audit should flag summary sections that cite missing answer IDs."""
    draft = SummaryDraft(
        markdown="## Résumé exécutif\n- Unsupported.",
        sections=[
            SummarySection(
                section_id="summary",
                title="Résumé exécutif",
                content="- Unsupported.",
                supporting_answer_ids=["missing"],
            )
        ],
    )
    state = BlackboardController().ingest([])

    findings = AdversarialAuditAgent().audit(draft, state)

    assert findings
    assert findings[0].required_action == "mark_unconfirmed"


def test_final_patch_does_not_append_rewrite_findings_as_claims() -> None:
    """LLM rewrite findings should remain warnings, not duplicated markdown."""
    draft = SummaryDraft(
        markdown="# Résumé de session\n\nLa bataille reste en suspens.",
        sections=[
            SummarySection(
                section_id="summary",
                title="Résumé de session",
                content="La bataille reste en suspens.",
                supporting_answer_ids=["a001"],
            )
        ],
    )
    findings = [
        AuditFinding(
            finding_id="audit_llm_000",
            severity=ConflictSeverity.CRITICAL,
            claim="Too much mechanical detail.",
            issue="Too much mechanical detail.",
            required_action="rewrite",
        )
    ]

    final = FinalPatchAgent().patch(draft, findings)

    assert "## Non-confirmed" not in final.markdown
    assert "Too much mechanical detail." not in final.markdown
    assert final.warnings == ["Too much mechanical detail."]


def test_character_verifier_neutralizes_forbidden_actor_attributions() -> None:
    """The final verifier should fix named actors for ambiguous hit events."""
    state = BlackboardController().ingest(
        [
            EvidenceAnswer(
                answer_id="neutral",
                question_id="combat",
                claim="A party member was hit by a crossbow bolt from the far bank.",
                status=FactStatus.SUPPORTED,
                importance=4,
                confidence=Confidence.HIGH,
                claim_type=ClaimType.COMBAT_OUTCOME,
                support=[_support()],
            ),
            EvidenceAnswer(
                answer_id="named",
                question_id="combat",
                claim="Ilùvatar was hit by a crossbow bolt from the far bank.",
                status=FactStatus.SUPPORTED,
                importance=4,
                confidence=Confidence.HIGH,
                claim_type=ClaimType.COMBAT_OUTCOME,
                support=[_support()],
            ),
        ],
    )
    final = FinalSummary(
        markdown=(
            "# Résumé de session\n\n"
            "Ilùvatar se téléporte au milieu du gué et est touché par une "
            "arbalète depuis la rive opposée.\n\n"
            "Garath agit en premier dans le sanctuaire."
        ),
        sections=[
            SummarySection(
                section_id="summary",
                title="Résumé de session",
                content=(
                    "Ilùvatar se téléporte au milieu du gué et est touché "
                    "par une arbalète."
                ),
                supporting_answer_ids=["neutral"],
            )
        ],
    )

    verified = CharacterAttributionVerifierAgent().verify(final, state)

    assert "Ilùvatar se téléporte" not in verified.markdown
    assert "un compagnon se téléporte" in verified.markdown
    assert "Garath agit en premier" in verified.markdown
    assert "un compagnon se téléporte" in verified.sections[0].content
    assert verified.metadata["character_attribution_verified"] is True
    assert verified.metadata["character_attribution_correction_count"] == 2


def test_orchestrator_runs_bounded_pipeline() -> None:
    """The orchestrator should run the full deterministic pipeline."""
    result = AnalysisOrchestrator(max_audit_attempts=3).run(_index())

    assert result.attempts <= 3
    assert result.plan.questions
    assert result.answers
    assert result.blackboard.facts
    assert result.final_summary.sections
    assert result.final_summary.llm_call_count == 0


def test_orchestrator_caps_empty_replan_loop() -> None:
    """The orchestrator should stop after the configured replan limit."""
    empty_index = EvidenceIndex.from_transcription(
        new_merged_transcription(text="", segments=[])
    )

    result = AnalysisOrchestrator(max_audit_attempts=2).run(empty_index)

    assert result.attempts == 2
    assert result.findings[0].required_action == "replan"
