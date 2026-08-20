"""Tests for parallel specialist execution."""

from __future__ import annotations

import time
from dataclasses import dataclass

from tara.analysis.agents import _run_specialists_with_usage
from tara.analysis.llm_runner import LLMRequest, LLMResponse, LLMRunner, LLMRunnerConfig
from tara.analysis.models import (
    AnalysisPlan,
    AnalysisQuestion,
    ClaimType,
    RetrievalQuery,
)


@dataclass(slots=True)
class _FakeRetriever:
    """Minimal retriever stub."""

    def retrieve(self, query: RetrievalQuery, limit: int | None = None) -> list:
        """Return no evidence so specialists use deterministic fallback."""
        return []


class _SlowBackend:
    """Backend that sleeps to make parallelism measurable."""

    backend_name = "api"

    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0

    def run(self, request: LLMRequest) -> LLMResponse:
        """Sleep and return a minimal YAML payload."""
        self.calls += 1
        time.sleep(self.delay_seconds)
        return LLMResponse(
            content="facts: []\nopen_questions: []\nrejected_noise: []",
            model="fake",
            backend="api",
        )


def _plan() -> AnalysisPlan:
    """Build a small specialist plan."""
    questions = [
        AnalysisQuestion(
            question_id=f"q{i}",
            priority=1,
            retrieval_queries=[
                RetrievalQuery(query_id=f"q{i}_00", text="combat", question_id=f"q{i}"),
            ],
            required_output_schema={"claim_type": ClaimType.CHRONOLOGY.value},
            responsible_agent="ChronologyAgent",
        )
        for i in range(3)
    ]
    return AnalysisPlan(questions=questions)


def _specialists(runner: LLMRunner) -> dict:
    from tara.analysis.agents import ChronologyAgent

    config = {"backend": "api", "cursor_cli_probe": True}
    return {"ChronologyAgent": ChronologyAgent(llm_runner=runner, config=config)}


def test_parallel_specialists_preserve_answer_count() -> None:
    """Parallel and sequential modes should emit the same number of answers."""
    backend = _SlowBackend(delay_seconds=0.01)
    runner = LLMRunner(
        LLMRunnerConfig(backend="api", model="fake"),
        api_backend=backend,
    )
    plan = _plan()
    retriever = _FakeRetriever()
    sequential_answers, _ = _run_specialists_with_usage(
        plan,
        retriever,
        _specialists(runner),
        parallel=False,
    )
    parallel_answers, _ = _run_specialists_with_usage(
        plan,
        retriever,
        _specialists(runner),
        parallel=True,
    )
    assert len(parallel_answers) == len(sequential_answers)
