# Subagent review report template

Copy this structure into `.cursor/skills/tara-review/review_process/reviews/task-<number>-<short-slug>/agent_<n>_<role>.md`.
Use **Composer 2** as the reviewer model unless the project lead specifies otherwise.

```markdown
# Agent <number> - <reviewer name>

Status: Approved | Changes requested
Reviewed task: <task title or identifier>
Review iteration: <number>
Reviewer model: Composer 2
Reviewed files:

- `<path>`

## Findings

- Severity: Critical | High | Medium | Low
  File: `<path>:<line>`
  Issue: <what is wrong>
  Impact: <why it matters>
  Required change: <smallest useful fix>

## Approval Notes

<Explain why the reviewer approves, or what residual risk remains.>
```

When a reviewer repeats after fixes, either bump `Review iteration` in the same
file or add a sibling file such as `agent_2_quality_code.iteration_2.md`, per
team preference. The task is not complete until every required agent shows
`Status: Approved`.
