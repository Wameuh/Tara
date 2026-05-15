"""Pydantic models for transcription API responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TranscriptionSegment(BaseModel):
    """Represents a segment of transcribed audio."""

    start: float = Field(..., description="Start time in seconds.")
    end: float = Field(..., description="End time in seconds.")
    text: str = Field(..., description="Transcribed text for the segment.")


class TranscriptionResponse(BaseModel):
    """API response model for a transcription request."""

    text: str = Field(..., description="Full concatenated transcript.")
    segments: list[TranscriptionSegment] = Field(
        default_factory=list,
        description="List of transcript segments with timing.",
    )
    language: str | None = Field(None, description="Detected or requested language.")
    duration: float | None = Field(None, description="Audio duration in seconds.")
    model: str = Field(..., description="Model identifier used for transcription.")

