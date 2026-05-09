# Plan de refactorisation Tara

Source d'architecture: [ARCHITECTURE.md](ARCHITECTURE.md)

Objectif: remplacer la partie analyse actuelle de Tara par l'architecture "Blackboard, retrieval local et audit adversarial", sans toucher a la transcription qui fonctionne deja.

## Perimetre

### Reference contracts from `../Tara`

- `src/tara/transcription/`: reference for transcription behavior; the old implementation is not modified.
- `src/inference_server/`: reference for the transcription inference server; the old implementation is not modified.
- `src/tara/processing/`: reference for the `merged_transcription.json` contract, preserved as the analysis input.
- `src/tara/cli/`: reference for CLI behavior and arguments.
- `src/tara/configuration/`: reference for JSON configuration patterns.
- `src/tara/logging/`, `src/tara/telemetry/`, `src/tara/usage_reporting/`: references for instrumentation and cost reporting.
- `src/tara/control/agent.py`: reference for orchestration responsibilities; TaraRepo creates its own equivalent.
- Transcription, inference server, and processing tests define non-regression expectations.

### A remplacer ou deprecier

- `src/tara/scene_analyzer/`: remplace par l'index local + planning + agents specialistes.
- `src/tara/transcription_splitter/`: plus obligatoire, les scenes ne sont plus la structure centrale.
- `src/tara/scene_descriptor/`: remplace par les facts specialises et la blackboard.
- `src/tara/scene_summarizer/`: logique de synthese a remplacer par un composer blackboard-aware.
- `src/tara/scene_verifier/`: remplace par `AdversarialAuditAgent`, `ArbitrationPanel` et verification ciblee.
- Tests associes aux anciens agents d'analyse: a convertir ou archiver selon pertinence.

### Nouvelle exigence LLM

Tous les appels LLM doivent passer par une couche commune capable de choisir entre:

- une API LLM classique;
- Cursor CLI en mode non interactif avec `agent -p`.

La voie `api` exige un modele explicite dans la configuration ou la requete. La
voie `cursor_cli` utilise `Auto` comme modele de repli quand aucun modele n'est
configure.

Le reste du pipeline ne doit pas connaitre le backend exact. Les agents demandent une completion structuree a un `LLMRunner`; le runner decide comment executer l'appel.

## Feuille de route

| Etape | Tache | Detail |
| --- | --- | --- |
| 00 | Inventaire et garde-fous de migration | [tmp/tasks/00_inventory_and_guardrails.md](tmp/tasks/00_inventory_and_guardrails.md) |
| 01 | Cartographie des modules conserves/remplaces | [tmp/tasks/01_module_mapping.md](tmp/tasks/01_module_mapping.md) |
| 02 | Couche commune LLM API/Cursor CLI | [tmp/tasks/02_llm_runner_api_cursor.md](tmp/tasks/02_llm_runner_api_cursor.md) |
| 03 | Modeles de donnees analyse et blackboard | [tmp/tasks/03_analysis_models.md](tmp/tasks/03_analysis_models.md) |
| 04 | EvidenceIndexAgent et retrieval local | [tmp/tasks/04_evidence_index_agent.md](tmp/tasks/04_evidence_index_agent.md) |
| 05 | AnalysisPlannerAgent | [tmp/tasks/05_analysis_planner_agent.md](tmp/tasks/05_analysis_planner_agent.md) |
| 06 | Agents specialistes avec retrieval | [tmp/tasks/06_specialist_agents.md](tmp/tasks/06_specialist_agents.md) |
| 07 | BlackboardController | [tmp/tasks/07_blackboard_controller.md](tmp/tasks/07_blackboard_controller.md) |
| 08 | ArbitrationPanel | [tmp/tasks/08_arbitration_panel.md](tmp/tasks/08_arbitration_panel.md) |
| 09 | SummaryComposerAgent | [tmp/tasks/09_summary_composer_agent.md](tmp/tasks/09_summary_composer_agent.md) |
| 10 | AdversarialAuditAgent et FinalPatchAgent | [tmp/tasks/10_audit_and_final_patch.md](tmp/tasks/10_audit_and_final_patch.md) |
| 11 | Orchestration ControlAgent, CLI et configuration | [tmp/tasks/11_orchestration_cli_config.md](tmp/tasks/11_orchestration_cli_config.md) |
| 12 | Migration des tests et benchmarks qualite/cout | [tmp/tasks/12_tests_benchmarks_acceptance.md](tmp/tasks/12_tests_benchmarks_acceptance.md) |
| 13 | Commit safety et review Cursor CLI | [tmp/tasks/13_commit_review_process.md](tmp/tasks/13_commit_review_process.md) |

## Ordre recommande

1. Poser les garde-fous et la cartographie avant tout changement de code.
2. Creer la couche `LLMRunner`, car tous les nouveaux agents en dependent.
3. Creer les modeles de donnees et l'index local.
4. Implementer planner + agents specialistes + blackboard.
5. Ajouter arbitrage avant synthese.
6. Ajouter composer, audit et patch final.
7. Brancher le nouveau pipeline dans l'equivalent TaraRepo de `ControlAgent`.
8. Mettre en place le protocole commit/review par subagents.
9. Migrer les tests et comparer sur `Record19`.

## Commit et review

La refactorisation doit reprendre les garde-fous utilises dans `../DiscordCalendarBot/AGENTS.md` et `review_process`, avec adaptation Tara:

- avant chaque commit, verifier explicitement les fichiers stages et l'absence de secrets;
- utiliser des `git add <path>` explicites autant que possible;
- inspecter `git status --short`, `git diff --cached --name-only` et `git diff --cached`;
- ne jamais committer `.env`, credentials, tokens, bases SQLite locales, logs, caches, sorties audio/transcription privees ou artefacts runtime;
- apres chaque etape, lancer les reviewers definis dans [tmp/tasks/13_commit_review_process.md](tmp/tasks/13_commit_review_process.md);
- les reviews doivent etre executees avec des subagents Cursor;
- le modele de review demande est Composer 2;
- chaque reviewer ecrit un rapport markdown dans `review_process/reviews/<task-folder>/`;
- une etape n'est complete que lorsque les reviewers requis ont `Status: Approved`.

## Definition de fini

La refactorisation est consideree terminee quand:

- la transcription et le processing produisent toujours `merged_transcription.json`;
- le nouveau pipeline produit `session_summary.json` et `session_summary.md`;
- chaque claim final possede des `supporting_answer_ids`;
- les claims critiques pointent vers des chunks bruts;
- les contradictions critiques sont resolues, bloquees ou formulees "non confirme";
- le backend LLM peut etre configure en `api` ou `cursor_cli`;
- `cursor_cli` utilise `agent -p` avec modele `Auto` par defaut;
- les tests de non-regression transcription/processing restent verts;
- un benchmark sur `Record19` compare cout estime, nombre d'appels LLM, support des claims et qualite du resume.
- chaque etape a des reviews subagent approuvees;
- le dernier commit mentionne que les fichiers stages ont ete verifies contre les secrets.

## Implementation Tracking

- `00` Inventory and migration guardrails: completed. The migration boundary,
  `merged_transcription.json` contract, `Record19` reference, private
  artifact/secret exclusions, and `run_tara.bat` compatibility reference are
  documented in `README.md` and
  `tmp/migration_inventory.md`.
- `02` Shared LLM runner API/Cursor CLI: completed. `LLMRunner` now exposes API
  and Cursor CLI backends through one typed interface, with mock-only tests,
  retries, usage parsing, cost estimation, and telemetry event support.
- `03` Analysis and blackboard data models: completed. Pydantic schemas now
  define merged transcription input, evidence, retrieval, planning, specialist
  answers, blackboard facts, conflicts, arbitration, summaries, audit findings,
  and validation invariants.
- `04` Local evidence index and retrieval: completed. `EvidenceIndex` now builds
  overlapping timestamped chunks from merged transcription segments, adds local
  keyword/entity metadata, reports coverage, exports debug artifacts, and
  retrieves evidence with CPU-only lexical scoring.
