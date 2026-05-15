"""Tests for single-pass JSON repair after failed structured LLM parses."""

from __future__ import annotations

import json

from tara.analysis.agentic_llm import run_arbitration_llm, run_specialist_extraction
from tara.analysis.llm_runner import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.analysis.models import (
    AnalysisQuestion,
    ClaimType,
    Conflict,
    ConflictSeverity,
    EvidenceChunk,
    RetrievalQuery,
)


class _RepairThenParseBackend:
    """API backend stub: bad JSON on extraction, valid JSON on repair."""

    backend_name = "api"

    def __init__(self, repair_payload: str) -> None:
        """Store the JSON string returned for repair calls."""
        self._repair_payload = repair_payload
        self.purposes: list[str] = []

    def run(self, request: LLMRequest) -> LLMResponse:
        """Return invalid text for specialist, valid JSON for repair."""
        self.purposes.append(request.purpose)
        if request.purpose == "analysis.specialist_extraction":
            return LLMResponse(
                content="this is not json",
                model="gpt-test",
                backend="api",
                total_tokens=3,
            )
        if request.purpose == "analysis.specialist_extraction.json_repair":
            return LLMResponse(
                content=self._repair_payload,
                model="gpt-test",
                backend="api",
                total_tokens=40,
            )
        raise AssertionError(f"unexpected purpose {request.purpose!r}")


def test_specialist_extraction_uses_single_json_repair() -> None:
    """Invalid primary JSON triggers one repair call and parsed facts."""
    repair_json = json.dumps(
        {
            "facts": [
                {
                    "claim": "Le combat commence au temple.",
                    "type": "chronology",
                    "confidence": "high",
                    "supporting_chunk_ids": ["chunk_0001"],
                    "uncertainty": None,
                },
            ],
            "open_questions": [],
            "rejected_noise": [],
        },
        ensure_ascii=True,
    )
    backend = _RepairThenParseBackend(repair_json)
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=backend,
    )
    question = AnalysisQuestion(
        question_id="chronology",
        priority=3,
        retrieval_queries=[
            RetrievalQuery(query_id="rq1", text="combat", question_id="chronology"),
        ],
        responsible_agent="ChronologyAgent",
    )
    chunks = [
        EvidenceChunk(
            chunk_id="chunk_0001",
            start=0.0,
            end=10.0,
            text="Le combat commence au temple.",
            segment_ids=[0],
        ),
    ]
    answers, usage = run_specialist_extraction(
        runner,
        question=question,
        chunks=chunks,
        default_claim_type=ClaimType.CHRONOLOGY,
        is_critical_default=False,
    )

    assert "analysis.specialist_extraction.json_repair" in backend.purposes
    assert usage.calls == 2
    assert any("temple" in a.claim for a in answers)


def test_arbitration_repair_returns_verdict() -> None:
    """Arbitration path merges usage when repair supplies valid JSON."""
    verdict = (
        '{"is_contradiction":false,"outcome":"uncertain",'
        '"accepted_answer_ids":[],"rejected_answer_ids":[],'
        '"merged_claim":null,"basis":"weak"}'
    )

    class _ArbBackend:
        backend_name = "api"

        def run(self, request: LLMRequest) -> LLMResponse:
            if request.purpose == "analysis.arbitration":
                return LLMResponse(
                    content="```not json```",
                    model="gpt-test",
                    backend="api",
                    total_tokens=2,
                )
            if request.purpose.endswith(".json_repair"):
                return LLMResponse(
                    content=verdict,
                    model="gpt-test",
                    backend="api",
                    total_tokens=20,
                )
            raise AssertionError(f"unexpected purpose {request.purpose!r}")

    backend = _ArbBackend()
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=backend,
    )
    conflict = Conflict(
        conflict_id="c1",
        answer_ids=["a1", "a2"],
        severity=ConflictSeverity.MINOR,
        description="test",
    )
    parsed, usage = run_arbitration_llm(
        runner,
        conflict=conflict,
        fact_rows=[{"answer_id": "a1", "claim": "x"}],
    )
    assert parsed is not None
    assert parsed.outcome == "uncertain"
    assert usage.calls == 2
