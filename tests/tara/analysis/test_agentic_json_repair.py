"""Tests for single-pass YAML repair after failed structured LLM parses."""

from __future__ import annotations

import json

from tara.analysis.agentic_llm import (
    run_arbitration_llm,
    run_audit_llm,
    run_composer_llm,
    run_specialist_extraction,
)
from tara.analysis.agents import AnalysisOrchestrator
from tara.analysis.evidence_index import EvidenceIndex
from tara.analysis.llm_runner import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.analysis.models import (
    AnalysisQuestion,
    ClaimType,
    Conflict,
    ConflictSeverity,
    EvidenceChunk,
    RetrievalQuery,
)
from tara.schemas.merged_transcription import new_merged_transcription
from tara.yaml_utils import to_yaml


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
        if request.purpose == "analysis.specialist_extraction.yaml_repair":
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

    assert "analysis.specialist_extraction.yaml_repair" in backend.purposes
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
            if request.purpose.endswith(".yaml_repair"):
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


def test_context_is_injected_into_primary_prompts_but_not_json_repair() -> None:
    """General context should guide extraction while repair stays format-only."""
    repair_json = json.dumps(
        {
            "facts": [],
            "open_questions": [],
            "rejected_noise": [],
        },
    )
    backend = _RepairThenParseBackend(repair_json)
    prompts: dict[str, str] = {}

    original_run = backend.run

    def capture(request: LLMRequest) -> LLMResponse:
        prompts[request.purpose] = f"{request.system_prompt}\n{request.user_prompt}"
        return original_run(request)

    backend.run = capture  # type: ignore[method-assign]
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
            text="[willygorn] Le combat commence.",
            segment_ids=[0],
        ),
    ]

    run_specialist_extraction(
        runner,
        question=question,
        chunks=chunks,
        default_claim_type=ClaimType.CHRONOLOGY,
        is_critical_default=False,
        context_text="willygorn plays Karknyr.",
    )

    assert "willygorn plays Karknyr" in prompts["analysis.specialist_extraction"]
    assert (
        "[willygorn] Le combat commence." in prompts["analysis.specialist_extraction"]
    )
    assert (
        "Do not extrapolate beyond the transcript"
        in (prompts["analysis.specialist_extraction"])
    )
    assert (
        "never present a guess as fact" in (prompts["analysis.specialist_extraction"])
    )
    assert (
        "willygorn plays Karknyr"
        not in prompts["analysis.specialist_extraction.yaml_repair"]
    )


def test_composer_receives_prior_then_general_context() -> None:
    """Composer prompt receives prior context before general naming context."""

    class _ComposerBackend:
        backend_name = "api"

        def __init__(self) -> None:
            self.prompt = ""
            self.system_prompt = ""

        def run(self, request: LLMRequest) -> LLMResponse:
            self.prompt = request.user_prompt
            self.system_prompt = request.system_prompt
            payload = {
                "markdown": "# Résumé de session\n\n## Résumé express\n- X",
                "sections": [
                    {
                        "section_id": "resume",
                        "title": "Résumé express",
                        "content": "- X",
                        "supporting_answer_ids": ["a1"],
                    },
                ],
            }
            return LLMResponse(
                content=to_yaml(payload),
                model="gpt-test",
                backend="api",
            )

    backend = _ComposerBackend()
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=backend,
    )

    run_composer_llm(
        runner,
        facts_payload=[{"answer_id": "a1", "claim": "X"}],
        do_not_claim=[],
        context_text="willygorn plays Karknyr.",
        prior_context_text="Previous temple summary.",
    )

    assert backend.system_prompt.index("Previous temple summary.") < (
        backend.system_prompt.index("willygorn plays Karknyr.")
    )
    assert "Use character names and MJ" in backend.prompt
    assert "remind players what happened before the next session" in backend.prompt
    assert "Combat summarization policy (critical)" in backend.system_prompt
    assert "finit par les vaincre" in backend.system_prompt
    assert "Do not include dice rolls, attack totals" in backend.prompt
    assert "Do not extrapolate beyond the transcript" in backend.system_prompt
    assert "never present a guess as fact" in backend.system_prompt


def test_audit_and_arbitration_receive_general_context() -> None:
    """Audit and arbitration prompts should include general naming context."""

    class _PromptBackend:
        backend_name = "api"

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run(self, request: LLMRequest) -> LLMResponse:
            self.prompts.append(
                f"{request.system_prompt}\n{request.user_prompt}",
            )
            if request.purpose == "analysis.adversarial_audit":
                content = '{"approved":true,"issues":[]}'
            elif request.purpose == "analysis.arbitration":
                content = (
                    '{"is_contradiction":false,"outcome":"accepted",'
                    '"accepted_answer_ids":["a1"],"rejected_answer_ids":[],'
                    '"merged_claim":null,"basis":"ok"}'
                )
            else:
                raise AssertionError(request.purpose)
            return LLMResponse(content=content, model="gpt-test", backend="api")

    backend = _PromptBackend()
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=backend,
    )
    context = "wameuh is the MJ."
    run_audit_llm(
        runner,
        draft_markdown="# Résumé de session",
        facts_payload=[],
        do_not_claim=[],
        context_text=context,
    )
    run_arbitration_llm(
        runner,
        conflict=Conflict(
            conflict_id="c1",
            answer_ids=["a1", "a2"],
            severity=ConflictSeverity.MINOR,
            description="test",
        ),
        fact_rows=[{"answer_id": "a1", "claim": "x"}],
        context_text=context,
    )

    assert all(context in prompt for prompt in backend.prompts)
    assert "over-focuses on low-impact mechanics" in backend.prompts[0]
    assert "'Résumé express' reads like a complete recap" in backend.prompts[0]
    assert "two compact sentences" in backend.prompts[0]
    assert "temporary hit point amounts" in backend.prompts[0]
    assert "turret/ballista mechanics" in backend.prompts[0]
    assert "first substantive content" in backend.prompts[0]
    assert "answer IDs, chunk IDs, scene IDs" in backend.prompts[0]
    assert all(
        "Do not extrapolate beyond the transcript" in prompt
        for prompt in backend.prompts
    )
    assert all("never present a guess as fact" in prompt for prompt in backend.prompts)


def test_orchestrator_wires_context_to_real_agent_llm_calls() -> None:
    """The full agent loop should pass context through every real LLM stage."""

    class _OrchestratorBackend:
        backend_name = "api"

        def __init__(self) -> None:
            self.requests: list[LLMRequest] = []
            self.specialist_calls = 0

        def run(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            if request.purpose == "analysis.specialist_extraction":
                self.specialist_calls += 1
                if self.specialist_calls == 1:
                    return LLMResponse(
                        content="not json",
                        model="gpt-test",
                        backend="api",
                    )
                return LLMResponse(
                    content=self._specialist_payload(request),
                    model="gpt-test",
                    backend="api",
                )
            if request.purpose == "analysis.specialist_extraction.yaml_repair":
                return LLMResponse(
                    content=to_yaml(
                        {
                            "facts": [
                                {
                                    "claim": "Karknyr entre dans le temple.",
                                    "type": "chronology",
                                    "confidence": "high",
                                    "supporting_chunk_ids": ["chunk_0000"],
                                    "uncertainty": None,
                                },
                            ],
                            "open_questions": [],
                            "rejected_noise": [],
                        },
                    ),
                    model="gpt-test",
                    backend="api",
                )
            if request.purpose == "analysis.arbitration":
                return LLMResponse(
                    content=(
                        '{"is_contradiction":true,"outcome":"do_not_claim",'
                        '"accepted_answer_ids":[],"rejected_answer_ids":[],'
                        '"merged_claim":null,"basis":"conflict"}'
                    ),
                    model="gpt-test",
                    backend="api",
                )
            if request.purpose == "analysis.summary_composer":
                return LLMResponse(
                    content=to_yaml(
                        {
                            "markdown": "# Résumé de session\n\n"
                            "## Résumé express\n- Karknyr avance.",
                            "sections": [
                                {
                                    "section_id": "resume",
                                    "title": "Résumé express",
                                    "content": "- Karknyr avance.",
                                    "supporting_answer_ids": ["chronology_llm_00"],
                                },
                            ],
                        },
                    ),
                    model="gpt-test",
                    backend="api",
                )
            if request.purpose == "analysis.adversarial_audit":
                return LLMResponse(
                    content='{"approved":true,"issues":[]}',
                    model="gpt-test",
                    backend="api",
                )
            raise AssertionError(f"unexpected purpose {request.purpose}")

        @staticmethod
        def _specialist_payload(request: LLMRequest) -> str:
            qid = str(request.metadata.get("question_id", "chronology"))
            if qid == "combat_outcome":
                facts = [
                    {
                        "claim": "Molnir est mort.",
                        "type": "combat_outcome",
                        "confidence": "high",
                        "supporting_chunk_ids": ["chunk_0000"],
                        "uncertainty": None,
                    },
                    {
                        "claim": "Molnir survit.",
                        "type": "combat_outcome",
                        "confidence": "medium",
                        "supporting_chunk_ids": ["chunk_0000"],
                        "uncertainty": None,
                    },
                ]
            else:
                facts = [
                    {
                        "claim": f"Fait soutenu pour {qid}.",
                        "type": "chronology",
                        "confidence": "high",
                        "supporting_chunk_ids": ["chunk_0000"],
                        "uncertainty": None,
                    },
                ]
            return to_yaml(
                {"facts": facts, "open_questions": [], "rejected_noise": []},
            )

    backend = _OrchestratorBackend()
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="gpt-test", max_retries=0),
        api_backend=backend,
    )
    transcription = new_merged_transcription(
        text=(
            "Karknyr entre dans le temple. Molnir est mort. "
            "Molnir survit. Une potion soigne le groupe."
        ),
        segments=[
            {
                "start": 0.0,
                "end": 30.0,
                "text": (
                    "Karknyr entre dans le temple. Molnir est mort. "
                    "Molnir survit. Une potion soigne le groupe."
                ),
            },
        ],
        duration=30.0,
    )
    index = EvidenceIndex.from_transcription(transcription, 60.0, 0.0)
    general_context = "willygorn plays Karknyr."
    prior_context = "Previous temple summary."

    AnalysisOrchestrator(
        max_audit_attempts=1,
        llm_runner=runner,
        specialist_config={
            "backend": "api",
            "context_text": general_context,
            "prior_context_text": prior_context,
        },
    ).run(index)

    prompts_by_purpose: dict[str, list[str]] = {}
    for request in backend.requests:
        combined = f"{request.system_prompt}\n{request.user_prompt}"
        prompts_by_purpose.setdefault(request.purpose, []).append(combined)

    for purpose in [
        "analysis.specialist_extraction",
        "analysis.arbitration",
        "analysis.summary_composer",
        "analysis.adversarial_audit",
    ]:
        assert any(general_context in prompt for prompt in prompts_by_purpose[purpose])
    assert any(
        prior_context in prompt
        for prompt in prompts_by_purpose["analysis.summary_composer"]
    )
    for purpose, prompts in prompts_by_purpose.items():
        if purpose != "analysis.summary_composer":
            assert all(prior_context not in prompt for prompt in prompts)
    assert all(
        general_context not in prompt and prior_context not in prompt
        for prompt in prompts_by_purpose["analysis.specialist_extraction.yaml_repair"]
    )
