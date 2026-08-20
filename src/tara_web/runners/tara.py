"""Web adapter factory for the real Tara runner."""

from __future__ import annotations

from tara.web_contracts import TaraWebRunner as RunnerProtocol


def create_tara_runner() -> RunnerProtocol:
    """Import the engine lazily after the worker process has started."""
    from tara.web_runner import TaraWebRunner

    return TaraWebRunner()
