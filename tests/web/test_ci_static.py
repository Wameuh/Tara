from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_COMMIT = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load_workflow(name: str) -> dict[str, Any]:
    workflow = YAML(typ="safe").load(
        (WORKFLOWS / name).read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    return workflow


def action_references(workflow: dict[str, Any]) -> list[str]:
    return [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]


def test_ci_has_reproducible_required_checks() -> None:
    workflow = load_workflow("ci.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert set(workflow["jobs"]) == {
        "python",
        "frontend",
        "e2e-chromium",
        "container",
    }
    rendered = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    for required in (
        "uv sync --frozen",
        "umask 077",
        "uv run pytest",
        "npm ci --ignore-scripts",
        "npm run check",
        "playwright install --with-deps chromium",
        "docker compose config --quiet",
        "docker build",
    ):
        assert required in rendered


def test_release_verifies_hardened_compose_without_publishing() -> None:
    workflow = load_workflow("release.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert set(workflow["jobs"]) == {"browser-matrix", "compose-smoke"}
    rendered = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "playwright install --with-deps chromium firefox webkit" in rendered
    assert "npm run test:e2e" in rendered
    assert "scripts/smoke-web-compose.sh" in rendered
    assert "docker push" not in rendered


def test_third_party_actions_are_pinned_to_full_commits() -> None:
    references = [
        reference
        for name in ("ci.yml", "release.yml")
        for reference in action_references(load_workflow(name))
    ]
    assert references
    assert all(FULL_COMMIT.fullmatch(reference) for reference in references)
