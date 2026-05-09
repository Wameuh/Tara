# Benchmark - Cursor CLI pipeline probe

Status: Procedure (automated tests use a mocked Cursor backend)
Audience: operators benchmarking the new Tara pipeline against Cursor CLI

## What is measured

The optional **pipeline probe** issues exactly **one** `LLMRunner` request with
purpose `pipeline.cursor_cli_probe` before the deterministic blackboard loop.
Specialist agents still run without LLM today; the probe is the controlled way to
exercise `agent -p` subprocess wiring, timeouts, allow-listed environment, and
usage fields in `session_summary.json`.

Recorded fields (after a full run):

- `usage.llm_call_count` (includes the probe when enabled)
- `usage.estimated_llm_tokens`
- `usage.estimated_cost_usd`
- `usage.backend` → `cursor_cli` when the configured analysis backend is
  `cursor_cli` and at least one LLM call was recorded

## Enable the probe

1. **JSON config** — set `analysis.llm.backend` to `cursor_cli` and
   `analysis.llm.cursor_cli_probe` to `true` (see root `config/configuration.json`
   for the default shape).

2. **Environment override** — set `TARA_CURSOR_CLI_PROBE` to `1`, `true`, `yes`, or
   `on` to force the probe even when `cursor_cli_probe` is `false` in JSON (useful
   for one-off local runs without editing files).

## Example (local, private merged JSON)

```powershell
conda activate DM
$env:PYTHONPATH = "src"
$env:TARA_CURSOR_CLI_PROBE = "1"
python -m tara --merged-transcription "C:\path\to\merged_transcription.json" --analysis-backend cursor_cli
```

Do **not** commit private transcripts, `.env`, or generated `analysis/` outputs.

## Optional real end-to-end test

With the same layout as above, set `TARA_CURSOR_CLI_E2E=1` and run pytest; the
suite includes `test_real_cursor_cli_probe_smoke`, which invokes the real Cursor
CLI once and expects `usage.llm_call_count >= 1`. This is skipped by default
because it requires a working `agent` on `PATH`, network where applicable, and
can be slow.

## Automated coverage

`tests/tara/test_pipeline_cursor_cli_probe.py` mocks the Cursor subprocess layer
while asserting merged usage and `usage.backend` for the `cursor_cli` path.
