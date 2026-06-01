"""Standalone orchestration for Tara transcription and blackboard analysis."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tara.acceptance import evaluate_acceptance
from tara.analysis import (
    AnalysisOrchestrator,
    EvidenceIndex,
    LLMRequest,
    LLMRunner,
    LLMRunnerConfig,
    LLMRunnerError,
    MergedTranscription,
    PipelineResult,
)
from tara.analysis.scenes import SceneAnalysisPipeline, SceneBlackboardIngestor
from tara.analysis.scenes.models import ScenePipelineResult, SceneTimeline
from tara.cli import TaraArgs
from tara.config import TaraConfig, find_default_config_path, load_config
from tara.context import (
    AnalysisContext,
    load_analysis_context,
    resolve_context_path,
    write_context_debug_file,
)
from tara.logging_config import apply_logging_config
from tara.transcription import process_transcriptions, transcribe_audio_directory

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CursorCliProbeStats:
    """Token accounting for the optional Cursor CLI pipeline probe."""

    calls: int
    tokens: int
    cost_usd: float


class TaraPipelineError(RuntimeError):
    """Raised when a run cannot proceed with the provided pipeline inputs."""


@dataclass(frozen=True, slots=True)
class TaraRunResult:
    """Paths and status values produced by one Tara run."""

    merged_transcription_path: Path | None
    analysis_output_dir: Path | None
    session_summary_markdown_path: Path | None
    session_summary_json_path: Path | None
    attempts: int = 0
    warning_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the run result to a JSON-compatible dictionary."""
        return {
            "merged_transcription_path": _path_to_str(self.merged_transcription_path),
            "analysis_output_dir": _path_to_str(self.analysis_output_dir),
            "session_summary_markdown_path": _path_to_str(
                self.session_summary_markdown_path,
            ),
            "session_summary_json_path": _path_to_str(self.session_summary_json_path),
            "attempts": self.attempts,
            "warning_count": self.warning_count,
        }


class TaraControlAgent:
    """Coordinate transcription, processing, indexing, and analysis."""

    def __init__(self, args: TaraArgs, config: TaraConfig | None = None) -> None:
        """Initialize the standalone control agent."""
        self._args = args
        self._config_base_dir = _config_base_dir(args.config)
        self._config = config or load_config(args.config)
        _apply_cli_overrides(self._config, args)

    def run(self) -> TaraRunResult:
        """Run the configured standalone Tara pipeline."""
        apply_logging_config(self._config.logging)
        merged_transcription_path = self._resolve_merged_transcription()
        if self._args.skip_analysis or not self._config.analysis.enabled:
            return TaraRunResult(
                merged_transcription_path=merged_transcription_path,
                analysis_output_dir=None,
                session_summary_markdown_path=None,
                session_summary_json_path=None,
            )
        if merged_transcription_path is None:
            raise TaraPipelineError(
                "No merged transcription is available for analysis.",
            )
        return self._run_analysis(merged_transcription_path)

    def _resolve_merged_transcription(self) -> Path | None:
        """Resolve or create the canonical merged transcription input."""
        if self._args.merged_transcription is not None:
            return self._args.merged_transcription
        if self._args.audio_dir is None:
            return None
        output_dir = self._args.audio_dir / self._config.transcription.output_dir
        if self._args.start_from == "evidence-index":
            merged_path = output_dir / self._config.processing.output_filename
            if not merged_path.exists():
                raise TaraPipelineError(
                    "Cannot start from evidence-index because "
                    f"merged transcription does not exist: {merged_path}",
                )
            return merged_path
        if self._args.start_from != "processing":
            LOGGER.info("Running transcription stage.")
            transcribe_audio_directory(self._args.audio_dir, self._config)
        LOGGER.info("Running processing stage.")
        merged_path = process_transcriptions(output_dir, self._config)
        if merged_path is None:
            raise TaraPipelineError(
                "Processing did not produce a merged transcription artifact.",
            )
        return merged_path

    def _run_analysis(self, merged_transcription_path: Path) -> TaraRunResult:
        """Build the evidence index and run the blackboard analysis pipeline."""
        transcription = MergedTranscription.from_json(merged_transcription_path)
        analysis_output_dir = _analysis_output_dir(
            merged_transcription_path,
            self._config,
        )
        analysis_output_dir.mkdir(parents=True, exist_ok=True)
        context = _load_context_for_run(
            args=self._args,
            config=self._config,
            config_base_dir=self._config_base_dir,
        )
        if self._args.write_context_debug:
            write_context_debug_file(analysis_output_dir / "context_debug.md", context)

        if self._config.analysis.llm.backend == "deterministic":
            llm_runner = None
            probe_stats = _CursorCliProbeStats(calls=0, tokens=0, cost_usd=0.0)
        else:
            llm_runner = _build_llm_runner(self._config)
            probe_stats = _maybe_run_cursor_cli_pipeline_probe(
                llm_runner,
                self._config,
            )

        scene_result = _run_scene_pipeline(
            transcription=transcription,
            merged_transcription_path=merged_transcription_path,
            config=self._config,
            llm_runner=llm_runner,
        )
        scene_answers = (
            SceneBlackboardIngestor().to_evidence_answers(scene_result.timeline)
            if self._config.analysis.scenes.inject_into_blackboard
            else []
        )

        index = EvidenceIndex.from_transcription(
            transcription,
            target_window_seconds=self._config.analysis.target_window_seconds,
            overlap_seconds=self._config.analysis.overlap_seconds,
            metadata={"pipeline": self._config.analysis.pipeline},
        )
        index.write_chunks_jsonl(analysis_output_dir / "evidence_chunks.jsonl")
        index.write_metadata_json(analysis_output_dir / "evidence_index_metadata.json")

        result = AnalysisOrchestrator(
            max_audit_attempts=self._config.analysis.max_audit_attempts,
            llm_runner=llm_runner,
            specialist_config={
                "backend": self._config.analysis.llm.backend,
                "cursor_cli_probe": bool(
                    self._config.analysis.llm.cursor_cli_probe
                    or _cursor_cli_probe_env_enabled(),
                ),
                "context_text": context.general.text or "",
                "prior_context_text": context.prior.text or "",
            },
        ).run(
            index,
            scene_timeline=scene_result.timeline,
            initial_answers=scene_answers,
        )
        result = _merge_cursor_cli_probe_into_result(result, probe_stats)
        result = _merge_scene_usage_into_result(result, scene_result)
        _write_pipeline_debug_artifacts(
            result=result,
            output_dir=analysis_output_dir,
            blackboard_path=self._args.blackboard_path,
            analysis_plan_path=self._args.analysis_plan_path,
        )
        markdown_path = (
            analysis_output_dir / self._config.analysis.summary_markdown_filename
        )
        json_path = analysis_output_dir / self._config.analysis.summary_json_filename
        markdown_path.write_text(result.final_summary.markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(
                _final_summary_payload(
                    result,
                    merged_transcription_path,
                    self._config,
                    context,
                    scene_result,
                ),
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return TaraRunResult(
            merged_transcription_path=merged_transcription_path,
            analysis_output_dir=analysis_output_dir,
            session_summary_markdown_path=markdown_path,
            session_summary_json_path=json_path,
            attempts=result.attempts,
            warning_count=len(result.final_summary.warnings) + context.warning_count,
        )


def run_from_args(args: TaraArgs) -> TaraRunResult:
    """Run Tara from parsed command-line arguments."""
    return TaraControlAgent(args).run()


def _apply_cli_overrides(config: TaraConfig, args: TaraArgs) -> None:
    """Apply CLI overrides to loaded configuration."""
    if args.analysis_backend:
        config.analysis.llm.backend = args.analysis_backend
    if args.analysis_model:
        config.analysis.llm.model = args.analysis_model
    if args.cursor_cli_probe:
        config.analysis.llm.cursor_cli_probe = True


def _analysis_output_dir(
    merged_transcription_path: Path,
    config: TaraConfig,
) -> Path:
    """Compute the analysis output directory for a merged transcription."""
    return merged_transcription_path.parent / config.analysis.output_dir


def _run_scene_pipeline(
    *,
    transcription: MergedTranscription,
    merged_transcription_path: Path,
    config: TaraConfig,
    llm_runner: LLMRunner | None,
) -> ScenePipelineResult:
    """Run the optional scene pipeline with fallback semantics."""
    try:
        return SceneAnalysisPipeline(config.analysis.scenes).run(
            transcription=transcription,
            merged_transcription_path=merged_transcription_path,
            llm_runner=llm_runner,
        )
    except Exception:
        if config.analysis.scenes.fail_on_scene_error:
            raise
        LOGGER.warning(
            "Scene pipeline failed; falling back to blackboard-only.",
            exc_info=True,
        )
        return ScenePipelineResult(timeline=SceneTimeline())


def _write_pipeline_debug_artifacts(
    *,
    result: PipelineResult,
    output_dir: Path,
    blackboard_path: Path | None,
    analysis_plan_path: Path | None,
) -> None:
    """Write traceability artifacts for the pipeline run."""
    plan_path = analysis_plan_path or output_dir / "analysis_plan.json"
    blackboard_debug_path = blackboard_path or output_dir / "blackboard.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    blackboard_debug_path.parent.mkdir(parents=True, exist_ok=True)
    result.plan.to_json(plan_path)
    blackboard_debug_path.write_text(
        json.dumps(
            {
                "facts": [fact.to_dict() for fact in result.blackboard.facts],
                "conflicts": [
                    conflict.to_dict() for conflict in result.blackboard.conflicts
                ],
                "do_not_claim_list": result.blackboard.do_not_claim_list,
                "decisions": [decision.to_dict() for decision in result.decisions],
                "findings": [finding.to_dict() for finding in result.findings],
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )


def _final_summary_payload(
    result: PipelineResult,
    merged_transcription_path: Path,
    config: TaraConfig,
    context: AnalysisContext,
    scene_result: ScenePipelineResult,
) -> dict[str, Any]:
    """Build the traceable `session_summary.json` payload."""
    acceptance = evaluate_acceptance(
        result,
        analysis_backend=_acceptance_backend_for_quality(config),
    )
    return {
        "summary": result.final_summary.to_dict(),
        "traceability": {
            "merged_transcription_path": str(merged_transcription_path),
            "context_path": context.general.path_str,
            "prior_context_path": context.prior.path_str,
            "scene_count": scene_result.scene_count,
            "scene_analysis_path": scene_result.scene_analysis_path,
            "scene_descriptions_path": scene_result.scene_descriptions_path,
            "attempts": result.attempts,
            "answer_count": len(result.answers),
            "fact_count": len(result.blackboard.facts),
            "conflict_count": len(result.blackboard.conflicts),
            "audit_finding_count": len(result.findings),
            "supporting_answer_ids": sorted(
                {
                    answer_id
                    for section in result.final_summary.sections
                    for answer_id in section.supporting_answer_ids
                },
            ),
        },
        "usage": {
            "llm_call_count": acceptance.llm_call_count,
            "probe_llm_call_count": acceptance.probe_llm_call_count,
            "analysis_llm_call_count": acceptance.analysis_llm_call_count,
            "composition_llm_call_count": acceptance.composition_llm_call_count,
            "audit_llm_call_count": acceptance.audit_llm_call_count,
            "estimated_llm_tokens": acceptance.estimated_llm_tokens,
            "estimated_cost_usd": acceptance.estimated_cost_usd,
            "scene_llm_call_count": scene_result.scene_llm_call_count,
            "backend": _usage_backend_label(
                configured_backend=config.analysis.llm.backend,
                llm_call_count=acceptance.llm_call_count,
            ),
        },
        "acceptance": acceptance.to_dict(),
    }


def _build_llm_runner(config: TaraConfig) -> LLMRunner:
    """Build a configured LLM runner for agentic analysis backends."""
    backend = config.analysis.llm.backend
    if backend not in {"api", "cursor_cli"}:
        raise ValueError(f"Unsupported analysis LLM backend: {backend}")
    raw_model = config.analysis.llm.model
    if backend == "cursor_cli":
        resolved: str | None = None if raw_model == "Auto" else raw_model
    elif backend == "api":
        if raw_model == "Auto":
            resolved = config.analysis.llm.default_api_model
            if not resolved:
                raise TaraPipelineError(
                    "analysis.llm.default_api_model must be set when backend is api "
                    "and model is Auto.",
                )
        else:
            resolved = raw_model
    return LLMRunner(
        LLMRunnerConfig(
            backend=backend,
            model=resolved,
            cursor_command=config.analysis.llm.cursor_command,
            cursor_args=tuple(config.analysis.llm.cursor_args),
            timeout_seconds=config.analysis.llm.timeout_seconds,
            max_retries=config.analysis.llm.retries,
        ),
    )


def _cursor_cli_probe_env_enabled() -> bool:
    """Return True when `TARA_CURSOR_CLI_PROBE` requests a probe without JSON edits."""
    return os.environ.get("TARA_CURSOR_CLI_PROBE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _maybe_run_cursor_cli_pipeline_probe(
    llm_runner: LLMRunner,
    config: TaraConfig,
) -> _CursorCliProbeStats:
    """Optionally run one Cursor CLI completion before deterministic analysis."""
    if config.analysis.llm.backend != "cursor_cli":
        return _CursorCliProbeStats(calls=0, tokens=0, cost_usd=0.0)
    if not (config.analysis.llm.cursor_cli_probe or _cursor_cli_probe_env_enabled()):
        return _CursorCliProbeStats(calls=0, tokens=0, cost_usd=0.0)
    return _run_cursor_cli_pipeline_probe(llm_runner)


def _run_cursor_cli_pipeline_probe(
    llm_runner: LLMRunner,
) -> _CursorCliProbeStats:
    """Execute a single non-transcript health check via `LLMRunner`."""
    LOGGER.info("Running Cursor CLI pipeline probe (agent -p).")
    user_prompt = "Health check: respond with OK only."
    request = LLMRequest(
        purpose="pipeline.cursor_cli_probe",
        system_prompt=(
            "You are a non-interactive health check for a local automation pipeline. "
            "Reply with exactly the two letters OK and nothing else."
        ),
        user_prompt=user_prompt,
    )
    try:
        response = llm_runner.run(request)
    except LLMRunnerError as exc:
        msg = (
            "Cursor CLI pipeline probe failed. Install or fix the Cursor CLI, "
            "or disable `analysis.llm.cursor_cli_probe` and unset "
            "`TARA_CURSOR_CLI_PROBE`."
        )
        raise TaraPipelineError(msg) from exc
    tokens = response.total_tokens
    if tokens <= 0:
        tokens = max(0, response.input_tokens) + max(0, response.output_tokens)
    cost = float(response.estimated_cost_usd or 0.0)
    return _CursorCliProbeStats(calls=1, tokens=max(tokens, 0), cost_usd=cost)


def _merge_cursor_cli_probe_into_result(
    result: PipelineResult,
    probe: _CursorCliProbeStats,
) -> PipelineResult:
    """Add probe usage counters into the final summary for JSON acceptance."""
    if probe.calls <= 0:
        return result
    fs = result.final_summary
    merged_cost = (fs.estimated_cost_usd or 0.0) + probe.cost_usd
    new_summary = fs.model_copy(
        update={
            "probe_llm_call_count": fs.probe_llm_call_count + probe.calls,
            "llm_call_count": fs.llm_call_count + probe.calls,
            "estimated_llm_tokens": fs.estimated_llm_tokens + probe.tokens,
            "estimated_cost_usd": merged_cost if merged_cost > 0 else None,
        },
    )
    return PipelineResult(
        plan=result.plan,
        answers=result.answers,
        blackboard=result.blackboard,
        decisions=result.decisions,
        draft=result.draft,
        findings=result.findings,
        final_summary=new_summary,
        attempts=result.attempts,
    )


def _merge_scene_usage_into_result(
    result: PipelineResult,
    scene_result: ScenePipelineResult,
) -> PipelineResult:
    """Add scene LLM usage counters into the final summary totals."""
    if scene_result.scene_llm_call_count <= 0:
        return result
    fs = result.final_summary
    scene_cost = float(scene_result.estimated_scene_cost_usd or 0.0)
    merged_cost = (fs.estimated_cost_usd or 0.0) + scene_cost
    new_summary = fs.model_copy(
        update={
            "analysis_llm_call_count": (
                fs.analysis_llm_call_count + scene_result.scene_llm_call_count
            ),
            "llm_call_count": fs.llm_call_count + scene_result.scene_llm_call_count,
            "estimated_llm_tokens": (
                fs.estimated_llm_tokens + scene_result.estimated_scene_llm_tokens
            ),
            "estimated_cost_usd": merged_cost if merged_cost > 0 else None,
            "metadata": {
                **fs.metadata,
                "scene_llm_call_count": scene_result.scene_llm_call_count,
            },
        },
    )
    return PipelineResult(
        plan=result.plan,
        answers=result.answers,
        blackboard=result.blackboard,
        decisions=result.decisions,
        draft=result.draft,
        findings=result.findings,
        final_summary=new_summary,
        attempts=result.attempts,
    )


def _acceptance_backend_for_quality(config: TaraConfig) -> str:
    """Map configuration to the backend label used for acceptance quality gates."""
    backend = config.analysis.llm.backend
    if backend == "deterministic":
        return "deterministic"
    if backend == "cursor_cli":
        if config.analysis.llm.cursor_cli_probe or _cursor_cli_probe_env_enabled():
            return "cursor_cli"
        return "deterministic"
    return "api"


def _usage_backend_label(*, configured_backend: str, llm_call_count: int) -> str:
    """Classify usage row for `session_summary.json` consumers."""
    if llm_call_count == 0:
        return "deterministic"
    if configured_backend == "cursor_cli":
        return "cursor_cli"
    if configured_backend == "api":
        return "api"
    return "analysis_llm"


def _path_to_str(path: Path | None) -> str | None:
    """Serialize an optional path."""
    return str(path) if path is not None else None


def _config_base_dir(config_path: Path | None) -> Path | None:
    """Return the base directory for relative config paths."""
    if config_path is not None:
        return config_path.resolve().parent
    default_config = find_default_config_path()
    return default_config.parent if default_config is not None else None


def _load_context_for_run(
    *,
    args: TaraArgs,
    config: TaraConfig,
    config_base_dir: Path | None,
) -> AnalysisContext:
    """Resolve and load optional run context files."""
    general_path = args.context_path or resolve_context_path(
        config.analysis.context_path,
        base_dir=config_base_dir,
    )
    prior_path = args.prior_context_path or resolve_context_path(
        config.analysis.prior_context_path,
        base_dir=config_base_dir,
    )
    return load_analysis_context(general_path=general_path, prior_path=prior_path)
