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
import os
import shutil
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


def _resolve_agent_executable() -> str:
    """Return a path to the Cursor ``agent`` CLI, or raise if not found."""
    found = shutil.which("agent")
    if found:
        return found
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidate = Path(local_app) / "cursor-agent" / "agent.cmd"
            if candidate.is_file():
                return str(candidate)
    msg = (
        "Cursor `agent` CLI not found. Install it or add it to PATH "
        "(Windows default: %LOCALAPPDATA%\\cursor-agent\\agent.cmd)."
    )
    raise RuntimeError(msg)


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
    since_ref: str | None,
) -> str:
    """Build the headless ``agent`` instruction for one reviewer."""
    agents_doc = (
        ".cursor/skills/tara-review/review_process/review_agents.md"
    )
    if since_ref:
        range_line = (
            f"**Revision range (exclusive..inclusive):** `{since_ref}..{commit_sha}`"
        )
        rng = f"{since_ref}..{commit_sha}"
        inspect_step = (
            "3. Inspect the cumulative change with "
            f"`git diff --stat {rng}` and `git diff {rng}` "
            "(not a single-commit show)."
        )
    else:
        range_line = f"**Git commit to review:** {commit_sha}"
        inspect_step = (
            f"3. Inspect the change with `git show --stat {commit_sha}` "
            f"and `git show {commit_sha}`."
        )
    return f"""You are a Cursor Agent in non-interactive print mode for TaraRepo.

**Critical:** Do not reply with meta offers (for example asking what to do next).
Immediately execute the numbered steps using your tools until the report file exists.

You are **{role_title}** (reviewer {agent_index} of 5) for a completed change.

**Task title:** {task_title}
{range_line}
**Review report file (create or overwrite):** {rel_report}

**Instructions:**
1. Open and follow the role-specific prompt in `{agents_doc}`
   under the heading "## {role_title}" (use the fenced prompt as your checklist).
2. Read `ARCHITECTURE.md` and `projet.md` for boundaries.
{inspect_step}
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


def _prompt_to_agent_argv_parts(prompt: str) -> list[str]:
    """Split the user prompt for the ``agent`` CLI.

    The Cursor ``agent`` binary treats a *single* argv string containing
    newlines as only the first line (the rest is dropped). Passing one argv
    fragment per line preserves the full text (the CLI joins fragments with
    spaces, which is acceptable for these instructions).

    Args:
        prompt: Full multiline instruction text.

    Returns:
        Non-empty list of argv fragments to append after fixed flags.
    """
    parts = prompt.split("\n")
    while parts and parts[-1] == "":
        parts.pop()
    return [fragment if fragment else " " for fragment in parts]


def _run_one_agent(
    *,
    workspace: Path,
    model: str,
    timeout_seconds: int,
    prompt: str,
) -> None:
    """Run ``agent -p`` once and stream stdout/stderr."""
    agent_exe = _resolve_agent_executable()
    workspace_resolved = str(workspace.resolve())
    print(f"\n--- Running agent ({model}) ---\n", flush=True)

    argv_tail = _prompt_to_agent_argv_parts(prompt)
    cmd: list[str] = [
        agent_exe,
        "-p",
        "--trust",
        "--force",
        "--sandbox",
        "disabled",
        "--workspace",
        workspace_resolved,
        "--model",
        model,
        "--output-format",
        "text",
        *argv_tail,
    ]
    # CreateProcess command-line limit on Windows (conservative).
    if sys.platform == "win32":
        approx = sum(len(a) for a in cmd) + 3 * len(cmd)
        if approx > 30_000:
            raise RuntimeError(
                "Review prompt is too long for the Windows agent command line; "
                "shorten prompts or extend the runner with a file-based transport."
            )
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
        help="Git revision at range end (default: HEAD); resolved for prompts",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "Optional base revision: review `git diff <since>..<commit>` instead "
            "of a single `git show <commit>`"
        ),
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
    reviews_dir.mkdir(parents=True, exist_ok=True)

    commit_sha = _git_one_line(workspace, "rev-parse", args.commit)
    since_resolved: str | None = None
    if args.since is not None:
        since_resolved = _git_one_line(workspace, "rev-parse", args.since)

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
            since_ref=since_resolved,
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
