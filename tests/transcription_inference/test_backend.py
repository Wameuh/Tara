"""Tests for backend factory and protocol."""

from __future__ import annotations

import pytest

from inference_server.backend import TranscriptionBackend, create_backend
from inference_server.faster_whisper_backend import FasterWhisperBackend
from inference_server.parakeet_backend import ParakeetBackend


def test_create_backend_faster_whisper() -> None:
    """Factory returns FasterWhisperBackend for regular models."""

    backend = create_backend("large-v3")
    assert isinstance(backend, FasterWhisperBackend)


def test_create_backend_parakeet() -> None:
    """Factory returns ParakeetBackend for prefixed models."""

    backend = create_backend("parakeet:nvidia/parakeet-tdt-0.6b-v3")
    assert isinstance(backend, ParakeetBackend)


def test_backend_protocol_compliance() -> None:
    """Verify both backends implement the protocol correctly."""

    # FasterWhisperBackend should have all required methods
    fw_backend = create_backend("large-v3")
    assert hasattr(fw_backend, "transcribe")
    assert hasattr(fw_backend, "stream_transcribe")
    assert hasattr(fw_backend, "release_all")
    assert callable(fw_backend.transcribe)
    assert callable(fw_backend.stream_transcribe)
    assert callable(fw_backend.release_all)

    # ParakeetBackend should have all required methods
    parakeet_backend = create_backend("parakeet:nvidia/parakeet-tdt-0.6b-v3")
    assert hasattr(parakeet_backend, "transcribe")
    assert hasattr(parakeet_backend, "stream_transcribe")
    assert hasattr(parakeet_backend, "release_all")
    assert callable(parakeet_backend.transcribe)
    assert callable(parakeet_backend.stream_transcribe)
    assert callable(parakeet_backend.release_all)


def test_create_backend_singleton() -> None:
    """Factory returns singleton instances."""

    backend1 = create_backend("large-v3")
    backend2 = create_backend("large-v3")
    assert backend1 is backend2

    backend3 = create_backend("parakeet:nvidia/parakeet-tdt-0.6b-v3")
    backend4 = create_backend("parakeet:nvidia/parakeet-tdt-0.6b-v3")
    assert backend3 is backend4

