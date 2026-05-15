"""Shared pytest fixtures for TaraRepo tests."""

from __future__ import annotations

import pytest


@pytest.fixture()
def disable_worker_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable inference-server worker subprocess for predictable tests.

    Worker mode is enabled by default on Windows for crash isolation; tests
    mock backends in-process and expect ``INFERENCE_USE_WORKER=0``.
    """
    monkeypatch.setenv("INFERENCE_USE_WORKER", "0")
