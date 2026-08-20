# Review process (TaraRepo)

This folder lives under the **Cursor project skill** `.cursor/skills/tara-review/`.
It holds **commit safety expectations**, **subagent review prompts**, and
**archived reviewer reports** for the Tara blackboard refactor.

| Document | Purpose |
| --- | --- |
| [review_agents.md](review_agents.md) | Five-role Composer 2 prompts, invocation checklist, Tara-specific focus |
| [report_template.md](report_template.md) | Markdown structure each reviewer writes to disk |
| `reviews/` | One directory per roadmap task, each with `agent_1` … `agent_5` reports |
| `benchmarks/` | Aggregate-only benchmark notes (no private transcript text) |
| [../scripts/README.md](../scripts/README.md) | Optional batch runner `run_cursor_review_agents.py` (`agent -p`, flags, cumulative `--since`) |

Planning details and acceptance criteria for this workflow live in
`tmp/tasks/13_commit_review_process.md`. Operational prompts are canonical in
`review_agents.md` so day-to-day work does not depend on scrolling the task spec.
