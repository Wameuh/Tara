"""Quality gate tests for optimization defaults."""

from __future__ import annotations

from pathlib import Path

from tara.config import TaraConfig
from tara.yaml_utils import load_yaml_or_json

GOLDEN_DIR = Path(__file__).parent / "golden"
REQUIRED_HEADINGS = (
    "# Résumé de session",
    "## Résumé express",
    "## Impacts pour la suite",
    "## État final et ressources",
)


def test_analysis_parallel_defaults_to_true() -> None:
    """Analysis parallel fan-out is enabled by default."""
    config = TaraConfig()
    assert config.analysis.parallel is True


def test_specialist_tool_defaults_to_disabled() -> None:
    """Optional specialist excerpt tool stays off until explicitly enabled."""
    config = TaraConfig()
    assert config.analysis.llm.cursor_cli_specialist_tool is False


def test_golden_record26_summary_has_required_headings() -> None:
    """Golden Record26 baseline must contain required player-facing headings."""
    markdown = (GOLDEN_DIR / "record26" / "session_summary.md").read_text(
        encoding="utf-8",
    )
    for heading in REQUIRED_HEADINGS:
        assert heading in markdown


def test_golden_record19_blackboard_has_supported_facts() -> None:
    """Golden Record19 baseline must retain supported facts."""
    payload = load_yaml_or_json(GOLDEN_DIR / "record19" / "blackboard.yaml")
    facts = payload.get("facts", [])
    supported = [
        fact
        for fact in facts
        if isinstance(fact, dict) and fact.get("status") == "supported"
    ]
    assert len(supported) >= 1
