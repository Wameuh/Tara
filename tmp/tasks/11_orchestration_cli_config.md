# 11 - Orchestration ControlAgent, CLI et configuration

## Objectif

Brancher le nouveau pipeline dans l'application standalone TaraRepo apres
`processing`, sans modifier l'ancien depot `Tara`. Les modules de l'ancien
depot servent de reference pour les contrats et les patterns, pas de cible
d'edition.

## A recuperer de Tara comme reference

- `src/tara/control/agent.py`
- `src/tara/cli/argument_parser.py`
- `src/tara/configuration/models.py`
- `config/configuration.json`
- logging, telemetry, usage reporting.

## A ne pas recuperer tel quel

- Enchainement actuel:
  - scene analyzer;
  - splitter;
  - descriptor;
  - summarizer;
  - verifier.

## Nouveau flow cible

```text
transcription -> processing -> evidence index -> analysis planner -> specialists
-> blackboard -> arbitration -> composer -> audit -> final patch -> summary final
```

## Taches

1. Ajouter config `analysis`.
2. Ajouter config `analysis.llm`:
   - backend: `api` ou `cursor_cli`;
   - model: `Auto` par defaut;
   - cursor command: `agent`;
   - cursor args: `["-p"]`;
   - timeout;
   - retries.
3. Ajouter flags CLI:
   - `--skip-analysis`;
   - `--analysis-backend`;
   - `--analysis-model`;
   - `--start-from transcription|processing|evidence-index`;
   - `--blackboard`;
   - `--analysis-plan`.
4. Creer l'equivalent TaraRepo de `ControlAgent`:
   - appeler ou reutiliser transcription et processing via les contrats
     documentes;
   - remplacer les stages analyse par le nouveau flow;
   - sauvegarder chaque artefact.
5. Gerer les erreurs de ce slice:
   - entree CLI/API invalide;
   - fichier `merged_transcription.json` absent ou invalide;
   - transcription locale ou traitement qui ne produit pas d'entree fusionnee;
   - indisponibilite du serveur d'inference de transcription.
   Les erreurs LLM/API, Cursor CLI, et un mode ou l'audit bloque totalement la
   publication sont reportes au futur slice LLM-assisted. Dans ce slice
   deterministe, l'audit patche la sortie en marquant les points critiques non
   confirmes, sauf si aucun draft ne peut etre produit.
6. Ajouter feature flag:
   - `analysis.pipeline = "blackboard_v1"`;
   - eventuellement `legacy_scene_pipeline`.

## Criteres de validation

- Le run complet part d'un dossier audio et produit le resume final.
- Le run peut aussi partir de `--merged-transcription`.
- La transcription n'est pas modifiee.
- Le backend LLM est configurable sans changer les agents.

## Implementation Status

Status: Completed

Notes:

- `tara.config` loads JSON configuration, auto-loads `.env`, and adds
  `analysis` / `analysis.llm` configuration with the `blackboard_v1` feature
  flag.
  `.env` is loaded from the TaraRepo project root, not from arbitrary process
  working directories.
- `tara.cli` adds the standalone `tara` entrypoint flags:
  `--skip-analysis`, `--analysis-backend`, `--analysis-model`,
  `--start-from transcription|processing|evidence-index`, `--blackboard`, and
  `--analysis-plan`.
- `tara.pipeline.TaraControlAgent` runs from either an audio directory or
  `--merged-transcription`, writes evidence-index artifacts, debug artifacts,
  `session_summary.md`, and traceable `session_summary.json`.
- The pipeline fails early with explicit input errors when a resume path is
  missing or processing does not produce `merged_transcription.json`.
- `tara.transcription` calls the configured local inference server and merges
  transcription JSON files using the documented Tara processing contract.
- `tara.server` exposes `GET /health`, `POST /v1/runs`, and `POST /v1/analysis`
  for server deployment. The server validates paths before orchestration, maps
  predictable input failures to HTTP 400, allows unauthenticated local requests,
  and requires `TARA_API_TOKEN` for non-local requests.
- `run_tara.bat` mirrors the local helper behavior by starting the old Tara
  inference server reference and then running `python -m tara`.
- `analysis.llm` is wired into the specialist injection boundary, but the current
  implementation remains deterministic and does not invoke LLM calls. LLM and
  Cursor CLI runtime failures are therefore backlog for the future LLM-assisted
  pass, not active Task 11 behavior.
- Audit findings currently patch the final summary by marking unresolved
  critical findings as non-confirmed; they do not block `session_summary.*`
  publication unless the loop cannot produce a draft at all.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
