---
name: tara-review
description: >-
  Runs Tara commit safety and five-role code review (security, quality,
  testing, integration, documentation) using prompts and templates in this skill.
  Use before merge or when the user asks
  for Tara reviews, Composer 2 review gates, or Cursor CLI agent review runs.
disable-model-invocation: true
---

# Tara review

## Where everything lives

All review assets are **inside this skill directory**:

- `review_process/review_agents.md` — five reviewer prompts + commit safety
- `review_process/report_template.md` — markdown skeleton for each report
- `review_process/reviews/` — one folder per task with `agent_1` … `agent_5`
- `review_process/benchmarks/` — aggregate benchmark notes
- `scripts/run_cursor_review_agents.py` — optional non-interactive `agent -p` runner

From the **Tara root**, these paths start with:
`.cursor/skills/tara-review/review_process/…`

## Workflow (human or IDE agent)

1. Finish the implementation task; run relevant checks from the repository root.
2. Open [review_process/review_agents.md](review_process/review_agents.md) and
   launch **five** Cursor subagents (Composer 2 per project rules), each writing
   one report under
   `.cursor/skills/tara-review/review_process/reviews/task-<n>-<slug>/`.
3. Use [review_process/report_template.md](review_process/report_template.md).
4. Commit safety: explicit `git add`, inspect `git diff --cached`, refuse secrets
   and private runtime artifacts (see checklist in `review_agents.md`).

## Optional: Cursor CLI batch runner

From Tara root, with `agent` on `PATH` and Cursor auth configured:

```bash
python .cursor/skills/tara-review/scripts/run_cursor_review_agents.py \
  --task-folder task-15-cli-prior-context-cursor-probe \
  --task-title "Example task title"
```

See [scripts/README.md](scripts/README.md) for flags (`--model`, `--max-agents`, etc.).
