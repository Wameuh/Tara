# 09 - SummaryComposerAgent

## Objectif

Composer le resume final depuis la blackboard, sans relire toute la transcription et sans inventer de facts.

## A recuperer de Tara

- Contrat de sortie du resume actuel:
  - `Resume Express`;
  - `Impacts Pour La Suite`;
  - `Etat Final Et Ressources`.
- Historique precedent et compression depuis `scene_summarizer/history_context.py` et `history_compression.py` si encore utile.
- Sauvegarde JSON + markdown.

## A ne pas recuperer tel quel

- Synthese depuis `scene_descriptions.json`.
- Retry de contrat base sur une prose non sourcee.
- Prompts qui autorisent des phrases sans support.

## Taches

1. Construire l'entree du composer:
   - facts chronologiques top importance;
   - facts de continuity;
   - etats finaux;
   - do-not-claim list;
   - decisions d'arbitrage;
   - historique precedent si fourni.
2. Appeler `LLMRunner`.
3. Exiger une sortie structuree:
   - markdown final;
   - sections;
   - claims;
   - `supporting_answer_ids` par phrase ou bullet.
4. Valider le contrat:
   - titres exacts;
   - longueur;
   - bullets autonomes;
   - aucun claim interdit;
   - support obligatoire.
5. Sauvegarder:
   - `session_summary.json`;
   - `session_summary.md`;
   - `summary_trace.json`.

## Criteres de validation

- Chaque phrase/bullet a des supports.
- Les claims critiques supportent un chemin vers des chunks bruts.
- Le format final reste proche de l'usage actuel.
- La sortie est lisible sans ids, mais le JSON garde la tracabilite.

## Implementation Status

Status: Completed

Notes:

- `SummaryComposerAgent` composes supported blackboard facts into French
  `Résumé exécutif` and `Points clés` sections.
- Each section is a `SummarySection` with `supporting_answer_ids`.
- Rejected and uncertain facts are excluded from composition through
  `do_not_claim`.
- The deterministic composer does not reread the transcript and does not call an
  LLM in this slice.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest "tests/tara/analysis"`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
