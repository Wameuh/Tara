#!/usr/bin/env python3
"""Run Tara review roles via Cursor Agent CLI (`agent -p`).

This script lives in the ``tara-review`` Cursor skill. It defaults the TaraRepo
git workspace to the repository root (four levels above ``scripts/``).

Example::

    python .cursor/skills/tara-review/scripts/run_cursor_review_agents.py \\
        --task-folder task-15-cli-prior-context-cursor-probe \\
        --task-title "CLI prior context and Cursor probe flags"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _tara_repo_root_from_script() -> Path:
    """Return TaraRepo root: parent of ``.cursor`` containing this file."""
    # scripts/run_*.py -> parents[4] == repo root (.cursor/skills/tara-review/scripts)
    return Path(__file__).resolve().parents[4]


def _skill_relative_report_prefix() -> str:
    """Return repo-relative prefix for review markdown paths."""
    return ".cursor/skills/tara-review/review_process/reviews"


def _git_one_line(workspace: Path, *args: str) -> str:
    """Run git in ``workspace`` and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {msg}")
    return (result.stdout or "").strip()


def _build_agent_prompt(
    *,
    agent_index: int,
    role_title: str,
    task_title: str,
    commit_sha: str,
    rel_report: str,
) -> str:
    """Build the headless ``agent`` instruction for one reviewer."""
    agents_doc = (
        ".cursor/skills/tara-review/review_process/review_agents.md"
    )
    return f"""You are a Cursor Agent in non-interactive print mode for TaraRepo.

You are **{role_title}** (reviewer {agent_index} of 5) for a completed change.

**Task title:** {task_title}
**Git commit to review:** {commit_sha}
**Review report file (create or overwrite):** {rel_report}

**Instructions:**
1. Open and follow the role-specific prompt in `{agents_doc}`
   under the heading "## {role_title}" (use the fenced prompt as your checklist).
2. Read `ARCHITECTURE.md` and `projet.md` for boundaries.
3. Inspect the change with `git show --stat {commit_sha}` and `git show {commit_sha}`.
4. Use `.cursor/skills/tara-review/review_process/report_template.md` for layout.
5. Write the **full** review report to `{rel_report}` using your file tools.
   Set `Reviewer model: auto (Cursor CLI agent -p)` in the header (not Composer 2),
   because this run was spawned via `agent --model auto`.
6. Set `Reviewed task:` to the task title above. List concrete file paths you
   inspected.
7. If there are no blocking issues, set `Status: Approved`. Otherwise set
   `Status: Changes requested`.

When the file is saved, print exactly one line to stdout: `DONE {rel_report}`
"""


def _run_one_agent(
    *,
    workspace: Path,
    model: str,
    timeout_seconds: int,
    prompt: str,
) -> None:
    """Run ``agent -p`` once and stream stdout/stderr."""
    cmd = [
        "agent",
        "-p",
        "--trust",
        "--workspace",
        str(workspace),
        "--model",
        model,
        "--output-format",
        "text",
        prompt,
    ]
    print(f"\n--- Running agent ({model}) ---\n", flush=True)
    result = subprocess.run(
        cmd,
        cwd=workspace,
        timeout=timeout_seconds,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise SystemExit(f"agent exited with code {result.returncode}")


def main() -> None:
    """Parse CLI flags and spawn sequential ``agent`` review runs."""
    default_workspace = _tara_repo_root_from_script()
    parser = argparse.ArgumentParser(
        description=(
            "Run Tara review roles via Cursor CLI (agent -p, non-interactive)."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=default_workspace,
        help="TaraRepo root (default: inferred from this script location)",
    )
    parser.add_argument(
        "--task-folder",
        required=True,
        help="Review directory under review_process/reviews/, e.g. task-15-...",
    )
    parser.add_argument(
        "--task-title",
        required=True,
        help="Human-readable task title stored in the review markdown",
    )
    parser.add_argument(
        "--commit",
        default="HEAD",
        help="Git revision to review (default: HEAD)",
    )
    parser.add_argument(
        "--model",
        default="auto",
        help="Cursor agent model id (default: auto). See `agent models`.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Per-agent wall-clock timeout (default: 900)",
    )
    parser.add_argument(
        "--max-agents",
        type=int,
        default=5,
        help="Run only the first N reviewers (1-5), for smoke tests (default: 5)",
    )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not (workspace / ".git").is_dir():
        parser.error(f"Not a git workspace: {workspace}")

    prefix = _skill_relative_report_prefix()
    reviews_dir = workspace / prefix / args.task_folder
    if not reviews_dir.is_dir():
        parser.error(f"Review directory does not exist: {reviews_dir}")

    commit_sha = _git_one_line(workspace, "rev-parse", args.commit)

    agents: tuple[tuple[str, str], ...] = (
        ("agent_1_cyber_security.md", "Agent 1 - Cyber Security Reviewer"),
        ("agent_2_quality_code.md", "Agent 2 - Quality Code Reviewer"),
        ("agent_3_testing_code.md", "Agent 3 - Testing Code Reviewer"),
        ("agent_4_integration_test.md", "Agent 4 - Integration Test Reviewer"),
        ("agent_5_documentation.md", "Agent 5 - Documentation Reviewer"),
    )
    if not 1 <= args.max_agents <= 5:
        parser.error("--max-agents must be between 1 and 5")

    selected = agents[: args.max_agents]
    for index, (filename, role_heading) in enumerate(selected, start=1):
        rel_report = f"{prefix}/{args.task_folder}/{filename}"
        prompt = _build_agent_prompt(
            agent_index=index,
            role_title=role_heading,
            task_title=args.task_title,
            commit_sha=commit_sha,
            rel_report=rel_report,
        )
        print(f"\n===== Agent {index}: {filename} =====", flush=True)
        _run_one_agent(
            workspace=workspace,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            prompt=prompt,
        )


if __name__ == "__main__":
    main()
