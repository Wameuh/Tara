"""Tests for Tara acceptance metrics."""

from __future__ import annotations

from tara.acceptance import evaluate_acceptance
from tara.analysis import (
    AnalysisOrchestrator,
    ArbitrationPanel,
    BlackboardController,
    ClaimType,
    Confidence,
    ConflictSeverity,
    EvidenceAnswer,
    EvidenceIndex,
    EvidenceSupport,
    FactStatus,
    FinalPatchAgent,
    MergedTranscription,
    PipelineResult,
    SummaryComposerAgent,
    TranscriptionSegment,
)


def test_acceptance_report_passes_for_supported_pipeline() -> None:
    """A deterministic sourced run satisfies hard acceptance invariants."""
    result = AnalysisOrchestrator().run(_acceptance_index())

    report = evaluate_acceptance(result)

    assert report.accepted
    assert report.summary_support_rate == 1.0
    assert report.forbidden_claim_leak_count == 0
    assert report.unsupported_critical_claim_count == 0
    assert report.llm_call_count == 0
    assert report.estimated_llm_tokens == 0


def test_acceptance_report_detects_forbidden_leaks() -> None:
    """Forbidden claims appearing in final prose are counted."""
    blackboard = BlackboardController().ingest([
        _answer("supported", "Karknyr reste debout.", FactStatus.SUPPORTED),
        _answer("rejected", "Karknyr est mort.", FactStatus.REJECTED),
    ])
    decisions = ArbitrationPanel().arbitrate(blackboard)
    draft = SummaryComposerAgent().compose(blackboard, decisions)
    leaked_draft = draft.model_copy(
        update={"markdown": f"{draft.markdown}\n- Karknyr est mort."},
    )
    final = FinalPatchAgent().patch(leaked_draft, [])
    baseline = AnalysisOrchestrator().run(_acceptance_index())
    result = PipelineResult(
        plan=baseline.plan,
        answers=[],
        blackboard=blackboard,
        decisions=decisions,
        draft=leaked_draft,
        findings=[],
        final_summary=final,
        attempts=1,
    )

    report = evaluate_acceptance(result)

    assert not report.accepted
    assert report.forbidden_claim_leak_count == 1


def _acceptance_index() -> EvidenceIndex:
    """Build a small index that satisfies standard deterministic queries."""
    transcription = MergedTranscription(
        text=(
            "Le combat commence au temple. "
            "La lance touche l'ennemi. "
            "Une potion de soin stabilise Karknyr."
        ),
        segments=[
            TranscriptionSegment(
                start=0.0,
                end=30.0,
                text="Le combat commence au temple.",
            ),
            TranscriptionSegment(
                start=30.0,
                end=60.0,
                text="La lance touche l'ennemi.",
            ),
            TranscriptionSegment(
                start=60.0,
                end=90.0,
                text="Une potion de soin stabilise Karknyr.",
            ),
        ],
        language="fr",
        duration=90.0,
    )
    return EvidenceIndex.from_transcription(transcription)


def _answer(
    answer_id: str,
    claim: str,
    status: FactStatus,
) -> EvidenceAnswer:
    """Create a fixture answer."""
    support = (
        [
            EvidenceSupport(
                chunk_id="chunk_0001",
                start=0.0,
                end=1.0,
                segment_ids=[0],
            )
        ]
        if status == FactStatus.SUPPORTED
        else []
    )
    return EvidenceAnswer(
        answer_id=answer_id,
        question_id="q",
        claim=claim,
        status=status,
        importance=4,
        support=support,
        confidence=Confidence.HIGH,
        claim_type=ClaimType.COMBAT_OUTCOME,
        is_critical=True,
        metadata={"severity": ConflictSeverity.CRITICAL.value},
    )
