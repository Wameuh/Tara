"""Security defaults shared by inference API tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def configured_inference_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run inference tests behind the same bearer boundary as production."""
    monkeypatch.setenv("INFERENCE_BEARER_TOKEN", "test-inference-token")
