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
- Le nombre de tokens LLM estime est inferieur au pipeline actuel sur `Record19`.
- Les tests hors analyse restent verts.
