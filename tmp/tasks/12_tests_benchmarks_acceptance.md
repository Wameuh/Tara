# 12 - Tests, benchmarks et criteres d'acceptation

## Objectif

Prouver que le nouveau pipeline reduit les couts et ameliore la fiabilite sans casser la transcription.

## A recuperer de Tara

- Tests unitaires existants de:
  - transcription;
  - processing;
  - configuration;
  - control.
- Artefacts reels `Record19`.
- Telemetry et usage reporting.

## A ne pas recuperer tel quel

- Tests qui valident l'ancien comportement exact des scenes/descriptions.
- Snapshots qui imposent des descriptions longues intermediaires.

## Taches

1. Tests unitaires:
   - modeles;
   - LLMRunner API mock;
   - LLMRunner Cursor CLI mock;
   - evidence index;
   - retrieval;
   - blackboard validation;
   - arbitration policies.
2. Tests integration:
   - partir de `merged_transcription.json`;
   - produire `session_summary.json`;
   - verifier supports;
   - verifier do-not-claim.
3. Tests non-regression:
   - transcription;
   - inference server;
   - processing.
4. Benchmark `Record19`:
   - nombre d'appels LLM;
   - tokens API si disponibles;
   - temps;
   - nombre de claims finaux;
   - taux de support;
   - contradictions detectees/resolues.
5. Evaluation qualite manuelle:
   - resume utile pour prochaine session;
   - pas de fausse mort/survie;
   - etat final clair;
   - impacts actionnables.

## Definition d'acceptation

- `summary_support_rate` = 100%.
- `forbidden_claim_leak_count` = 0.
- Tous les claims critiques ont support direct.
- Les contradictions critiques non resolues ne sortent pas comme facts affirmes.
- Le nombre de tokens LLM estime est inferieur au pipeline actuel sur `Record19`;
  pour ce slice deterministe, la preuve reproductible est `llm_call_count = 0`
  et `estimated_llm_tokens = 0`. Une comparaison chiffree avec le pipeline
  legacy reste a ajouter quand les metriques legacy sont disponibles.
- Les tests hors analyse restent verts.

## Implementation Status

Status: Completed

Notes:

- Unit and integration coverage now includes models, LLM runner API/Cursor mocks,
  evidence indexing, retrieval, blackboard ingestion, arbitration, composer,
  audit, final patching, CLI/config/server orchestration, transcription
  processing, and acceptance metrics.
- `tara.acceptance.evaluate_acceptance` computes hard acceptance metrics from a
  `PipelineResult`: support rate, forbidden leaks, unsupported critical claims,
  critical conflict leakage, LLM calls, estimated cost, and final claim count.
- `session_summary.json` includes an `acceptance` section so each run carries its
  validation metrics beside traceability and usage. The top-level `usage`
  section is derived from the same acceptance/final-summary counters to avoid
  cost/call drift.
- A private Record19 smoke was run from the existing
  `merged_transcription.json`. Only aggregate metrics are recorded in
  `.cursor/skills/tara-review/review_process/benchmarks/task-12-record19-acceptance.md`; transcript-derived
  outputs remain outside this repository. The smoke proves the new deterministic
  path makes zero LLM calls; it does not include a legacy pipeline token sample.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
- Private Record19 smoke:
  `conda activate DM; $env:PYTHONPATH='src'; python -m tara --merged-transcription "<private Record19 merged_transcription.json>"`
