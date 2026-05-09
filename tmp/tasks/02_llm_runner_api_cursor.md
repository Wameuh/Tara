# 02 - Couche commune LLM API/Cursor CLI

## Objectif

Creer une abstraction unique pour tous les appels LLM. Les agents ne doivent pas savoir si l'appel part vers une API ou vers Cursor CLI.

## A recuperer de Tara

- `src/tara/scene_analyzer/client.py` comme inspiration pour:
  - telemetry d'usage;
  - retries;
  - timeouts;
  - parsing usage tokens;
  - support provider OpenAI.
- `src/tara/usage_reporting/`
- `src/tara/telemetry/`
- configuration actuelle des modeles dans `config/configuration.json`.

## A ne pas recuperer tel quel

- le nom `analyze_scenes` du client actuel;
- la logique liee aux anciens agents scene;
- `max_output_tokens=200000` par defaut sans budget par usage;
- couplage direct OpenAI dans les agents.

## Fonctionnalite requise

Backends supportes:

- `api`: appel API LLM classique;
- `cursor_cli`: appel non-interactif via `agent -p`;

Modele par defaut Cursor CLI:

- `Auto`.

Exemple de configuration cible:

```json
{
  "analysis": {
    "llm": {
      "backend": "cursor_cli",
      "model": "Auto",
      "cursor_command": "agent",
      "cursor_args": ["-p"],
      "timeout_seconds": 900,
      "max_retries": 2
    }
  }
}
```

`max_retries` means retries after the first attempt. For example,
`max_retries: 2` allows up to three total attempts.

## Taches

1. Creer `LLMRunner` avec une methode stable:

```python
run(prompt: LLMRequest) -> LLMResponse
```

2. Definir `LLMRequest`:
   - `purpose`
   - `system_prompt`
   - `user_prompt`
   - `response_format`
   - `model`
   - `temperature`
   - `max_output_tokens`
   - `metadata`
3. Definir `LLMResponse`:
   - `content`
   - `model`
   - `backend`
   - `input_tokens`
   - `output_tokens`
   - `total_tokens`
   - `raw_usage`
4. Implementer backend API:
   - reutiliser les idees de l'ancien client;
   - garder telemetry;
   - garder retries.
5. Implementer backend Cursor CLI:
   - construire la commande `agent -p`;
   - injecter le prompt complet en argument ou stdin selon le comportement retenu;
   - utiliser modele `Auto` par defaut;
   - capturer stdout/stderr;
   - timeout strict;
   - erreur lisible si `agent` n'est pas disponible.
6. Ajouter tests unitaires avec subprocess mocke.

## Questions techniques tranchees pendant implementation

- Cursor CLI model selection remains outside the prompt payload. If no request
  or config model is provided, the Cursor backend reports model `Auto`.
- Cursor CLI prompts are passed through stdin by default to avoid leaking prompt
  text in process arguments. `cursor_prompt_transport="argv"` is available only
  as an explicit compatibility option.
- Cursor CLI receives a sanitized environment allow-list by default so API keys
  and unrelated secrets are not inherited.
- Successful Cursor CLI stderr is not copied into response metadata unless
  explicitly enabled with `include_cursor_stderr`.
- Telemetry metadata is allow-listed through `telemetry_metadata_keys`; all
  request metadata is dropped by default.

## Criteres de validation

- Un agent peut appeler `LLMRunner` sans connaitre le backend.
- `backend=api` marche avec mock.
- `backend=cursor_cli` lance bien `agent -p` en test mocke.
- `cursor_cli` reports `model` as `Auto` if no model is configured. The `api`
  backend requires an explicit model.
- La telemetry enregistre `purpose`, backend, modele et usage disponible.

## Implementation Status

Status: Completed

Notes:

- `src/tara/analysis/llm_runner.py` defines `LLMRunner`, `LLMRequest`,
  `LLMResponse`, `LLMRunnerConfig`, `ModelPricing`, `OpenAIAPIBackend`, and
  `CursorCLIBackend`.
- The API backend uses an OpenAI-compatible Responses API endpoint, requires an
  explicit model, reads the API key from configurable environment variables,
  parses token usage, and estimates cost from configured pricing.
- The Cursor CLI backend builds `agent -p` commands, defaults the model to
  `Auto` when no model is configured, sends prompts via stdin by default,
  sanitizes inherited environment variables, and reports subprocess failures
  with readable errors.
- `LLMRunner` retries transient backend failures with exponential backoff, treats
  `max_retries` as retries after the first attempt, and records purpose,
  backend, model, usage, cost, attempt, and allow-listed metadata through an
  optional telemetry recorder.
- Non-retryable HTTP client errors fail fast as configuration errors, while
  transport and retryable HTTP errors are represented as backend errors.
- Tests are mock-only and do not call real LLM APIs or Cursor CLI.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest "tests/tara/analysis/test_llm_runner.py"`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
