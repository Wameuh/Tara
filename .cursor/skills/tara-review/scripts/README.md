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

Options: `--workspace` (default TaraRepo root), `--commit`, `--model` (default
`auto`), `--timeout-seconds`, `--max-agents` (1–5 for smoke tests).

Requires the Cursor Agent CLI (`agent` on `PATH`) and a logged-in account
(`agent status`).
