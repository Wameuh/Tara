"""FastAPI server for Tara orchestration."""

from __future__ import annotations

import os
from pathlib import Path

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from tara.cli import TaraArgs
from tara.pipeline import TaraControlAgent, TaraPipelineError
from tara.prompt_security import PromptSecurityRejected, PromptSecurityUnavailable

app = FastAPI(title="Tara", version="0.1.0")


class RunRequest(BaseModel):
    """Request body for a standalone Tara run."""

    audio_dir: str | None = Field(default=None)
    merged_transcription: str | None = Field(default=None)
    config: str | None = Field(default=None)
    skip_analysis: bool = False
    analysis_backend: str | None = Field(default=None)
    analysis_model: str | None = Field(default=None)
    start_from: str | None = Field(default=None)


class RunResponse(BaseModel):
    """Response body for a Tara run."""

    merged_transcription_path: str | None
    analysis_output_dir: str | None
    session_summary_markdown_path: str | None
    session_summary_json_path: str | None
    attempts: int
    warning_count: int


@app.get("/health")
def health() -> dict[str, str]:
    """Return server health status."""
    return {"status": "ok"}


@app.post("/v1/runs", response_model=RunResponse)
def run_pipeline(
    request: Request,
    run_request: RunRequest,
    authorization: str | None = Header(default=None),
) -> RunResponse:
    """Run transcription/processing and optional blackboard analysis."""
    _authorize_request(request, authorization)
    try:
        result = TaraControlAgent(_request_to_args(run_request)).run()
    except Exception as exc:
        _raise_http_error(exc)
    return RunResponse(**result.to_dict())


@app.post("/v1/analysis", response_model=RunResponse)
def run_analysis(
    request: Request,
    run_request: RunRequest,
    authorization: str | None = Header(default=None),
) -> RunResponse:
    """Run analysis from an existing `merged_transcription.json` file."""
    _authorize_request(request, authorization)
    if run_request.merged_transcription is None:
        raise HTTPException(
            status_code=400,
            detail="`merged_transcription` is required for /v1/analysis.",
        )
    try:
        args = _request_to_args(run_request)
        result = TaraControlAgent(args).run()
    except Exception as exc:
        _raise_http_error(exc)
    return RunResponse(**result.to_dict())


def _request_to_args(request: RunRequest) -> TaraArgs:
    """Convert a server request into validated control-agent arguments."""
    if bool(request.audio_dir) == bool(request.merged_transcription):
        raise ValueError(
            "Provide exactly one input: `audio_dir` or `merged_transcription`.",
        )
    audio_dir = _validated_path(request.audio_dir, expect_dir=True)
    merged_transcription = _validated_path(
        request.merged_transcription,
        expect_file=True,
    )
    config = _validated_path(request.config, expect_file=True)
    valid_start_points = {None, "transcription", "processing", "evidence-index"}
    if request.start_from not in valid_start_points:
        raise ValueError(f"Unsupported start_from value: {request.start_from}")
    return TaraArgs(
        audio_dir=audio_dir,
        merged_transcription=merged_transcription,
        config=config,
        skip_analysis=request.skip_analysis,
        analysis_backend=request.analysis_backend,
        analysis_model=request.analysis_model,
        start_from=request.start_from,
        prior_context_path=None,
        cursor_cli_probe=False,
    )


def _validated_path(
    value: str | None,
    *,
    expect_dir: bool = False,
    expect_file: bool = False,
) -> Path | None:
    """Resolve and validate an optional request path."""
    if value is None:
        return None
    path = Path(value).resolve()
    if expect_dir and not path.is_dir():
        raise ValueError(f"Directory does not exist: {path}")
    if expect_file and not path.is_file():
        raise ValueError(f"File does not exist: {path}")
    return path


def _authorize_request(request: Request, authorization: str | None) -> None:
    """Require a bearer token for non-local requests or when configured."""
    configured_token = os.getenv("TARA_API_TOKEN")
    if configured_token:
        expected = f"Bearer {configured_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Invalid API token.")
        return
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403,
            detail="Set TARA_API_TOKEN before accepting non-local API requests.",
        )


def _raise_http_error(exc: Exception) -> None:
    """Translate predictable orchestration failures to HTTP responses."""
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, requests.RequestException):
        raise HTTPException(
            status_code=503,
            detail=f"Transcription inference request failed: {exc}",
        ) from exc
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, PromptSecurityRejected):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, PromptSecurityUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, FileNotFoundError | OSError | TaraPipelineError | ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc
