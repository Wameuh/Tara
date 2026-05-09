# Tara Analysis Package

This package contains the new blackboard-based Tara analysis pipeline.

## LLM Runner

`llm_runner.py` defines the shared LLM execution layer used by future agents.
Agents submit `LLMRequest` objects and receive `LLMResponse` objects without
depending on OpenAI or Cursor CLI directly.

Supported backends:

- `api`: OpenAI-compatible Responses API backend, implemented first.
- `cursor_cli`: Cursor CLI backend using `agent -p`, implemented behind the same
  interface and tested with mocked subprocess execution.

The `api` backend requires an explicit model in the request or runner
configuration. The `cursor_cli` backend reports `Auto` when neither request nor
configuration provides a model.

The runner records purpose, backend, model, token usage, estimated cost, retry
attempt, and allow-listed request metadata when a telemetry recorder is
provided. Metadata is dropped from telemetry unless its key is explicitly listed
in `LLMRunnerConfig.telemetry_metadata_keys`.

Cursor CLI prompts are sent through stdin by default to avoid exposing transcript
content in process arguments. The subprocess environment is allow-listed so API
keys and unrelated secrets are not inherited by default. Argument-based prompt
transport remains available through `cursor_prompt_transport="argv"` for
compatibility testing.
