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
   - `--start-from evidence-index`;
   - `--blackboard`;
   - `--analysis-plan`.
4. Creer l'equivalent TaraRepo de `ControlAgent`:
   - appeler ou reutiliser transcription et processing via les contrats
     documentes;
   - remplacer les stages analyse par le nouveau flow;
   - sauvegarder chaque artefact.
5. Gerer erreurs:
   - si LLM indisponible;
   - si Cursor CLI absent;
   - si contradiction critique non resolue;
   - si audit bloque la sortie.
6. Ajouter feature flag:
   - `analysis.pipeline = "blackboard_v1"`;
   - eventuellement `legacy_scene_pipeline`.

## Criteres de validation

- Le run complet part d'un dossier audio et produit le resume final.
- Le run peut aussi partir de `--merged-transcription`.
- La transcription n'est pas modifiee.
- Le backend LLM est configurable sans changer les agents.
