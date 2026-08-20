# Tara review skill scripts

## `run_cursor_review_agents.py`

Runs up to five sequential `agent -p --trust` invocations (non-interactive) so
each Tara reviewer role can refresh its markdown report under this skill’s
`review_process/reviews/<task-folder>/`.

Run from **TaraRepo root**:

```bash
python .cursor/skills/tara-review/scripts/run_cursor_review_agents.py \
  --task-folder task-15-cli-prior-context-cursor-probe \
  --task-title "CLI prior context and Cursor probe flags"
```

Options: `--workspace` (default TaraRepo root), `--commit` (end of range;
default `HEAD`), `--since` (optional base; when set, reviewers use
`git diff <since>..<commit>` instead of `git show <commit>`), `--model` (default
`auto`), `--timeout-seconds`, `--max-agents` (1–5 for smoke tests).

Cumulative review since a base commit:

```bash
python .cursor/skills/tara-review/scripts/run_cursor_review_agents.py \
  --task-folder review-since-a4841b3 \
  --task-title "Changes since a4841b3 (standalone orchestration)" \
  --since a4841b379c5f3c66016c3a664fd1f306acc6af90
```

On **Windows cmd.exe**, prefer equals form for flags (for example
`--since=a4841b379c5f3c66016c3a664fd1f306acc6af90`) so arguments are not split
incorrectly.

The runner passes `--force` and `--sandbox disabled` so reviewers can run
`git` and write report files without interactive approval. It splits the
prompt on newlines into multiple argv fragments because the `agent` CLI only
uses the first line when the prompt is a single argument containing newlines.

Requires the Cursor Agent CLI (`agent` on `PATH`, or the default install under
`%LOCALAPPDATA%\cursor-agent\`) and a logged-in account (`agent status`).
