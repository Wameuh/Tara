from __future__ import annotations

from tara.web_contracts import TaraWebRunner as RunnerProtocol

_FAKE_SCENARIOS = {
    "success",
    "failure",
    "retry",
    "timeout",
    "blocking",
    "crash",
    "missing",
    "corrupt",
    "oversized",
    "flood",
}


def create_runner(kind: str, *, scenario: str = "success") -> RunnerProtocol:
    if kind == "fake" and scenario in _FAKE_SCENARIOS:
        from .fake import FakeRunner

        return FakeRunner(mode=scenario)
    if kind == "tara" and scenario == "success":
        from .tara import create_tara_runner

        return create_tara_runner()
    raise ValueError("runner configuration is invalid")
