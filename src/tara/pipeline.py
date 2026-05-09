"""Standalone orchestration for Tara transcription and blackboard analysis."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tara.acceptance import evaluate_acceptance
from tara.analysis import (
    AnalysisOrchestrator,
    EvidenceIndex,
    LLMRunner,
    LLMRunnerConfig,
    MergedTranscription,
    PipelineResult,
)
from tara.cli import TaraArgs
from tara.config import TaraConfig, load_config
from tara.logging_config import apply_logging_config
from tara.transcription import process_transcriptions, transcribe_audio_directory

LOGGER = logging.getLogger(__name__)


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
            llm_runner=_build_llm_runner(self._config),
            specialist_config={"backend": self._config.analysis.llm.backend},
        ).run(index)
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
                _final_summary_payload(result, merged_transcription_path),
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
            warning_count=len(result.final_summary.warnings),
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


def _analysis_output_dir(
    merged_transcription_path: Path,
    config: TaraConfig,
) -> Path:
    """Compute the analysis output directory for a merged transcription."""
    return merged_transcription_path.parent / config.analysis.output_dir


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
) -> dict[str, Any]:
    """Build the traceable `session_summary.json` payload."""
    acceptance = evaluate_acceptance(result)
    return {
        "summary": result.final_summary.to_dict(),
        "traceability": {
            "merged_transcription_path": str(merged_transcription_path),
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
            "estimated_llm_tokens": acceptance.estimated_llm_tokens,
            "estimated_cost_usd": acceptance.estimated_cost_usd,
            "backend": (
                "deterministic"
                if acceptance.llm_call_count == 0
                else "analysis_llm"
            ),
        },
        "acceptance": acceptance.to_dict(),
    }


def _build_llm_runner(config: TaraConfig) -> LLMRunner:
    """Build a configured LLM runner without invoking it in deterministic mode."""
    backend = config.analysis.llm.backend
    if backend not in {"api", "cursor_cli"}:
        raise ValueError(f"Unsupported analysis LLM backend: {backend}")
    model = config.analysis.llm.model
    return LLMRunner(
        LLMRunnerConfig(
            backend=backend,
            model=None if model == "Auto" else model,
            cursor_command=config.analysis.llm.cursor_command,
            cursor_args=tuple(config.analysis.llm.cursor_args),
            timeout_seconds=config.analysis.llm.timeout_seconds,
            max_retries=config.analysis.llm.retries,
        ),
    )


def _path_to_str(path: Path | None) -> str | None:
    """Serialize an optional path."""
    return str(path) if path is not None else None
