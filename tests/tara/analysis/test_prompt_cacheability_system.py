"""Tests for stable system prompt construction."""

from __future__ import annotations

from tara.analysis.agentic_llm import (
    build_audit_system_prompt,
    build_composer_system_prompt,
)


def test_composer_and_audit_share_identical_system_prompt_for_same_context() -> None:
    """Composer and audit should share cacheable system prompt text."""
    context = "MJ: Willy\nPC: Aldrik"
    composer_prompt = build_composer_system_prompt(
        context_text=context,
        prior_context_text="Previous session recap.",
    )
    audit_prompt = build_audit_system_prompt(context_text=context)
    assert "Evidence strictness policy:" in composer_prompt
    assert "Speaker attribution policy:" in composer_prompt
    assert "Evidence strictness policy:" in audit_prompt
    assert composer_prompt != audit_prompt
