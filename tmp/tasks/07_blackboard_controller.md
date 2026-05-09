# 07 - BlackboardController

## Objectif

Centraliser, valider, dedoublonner et classer les facts produits par les agents specialistes.

## A recuperer de Tara

- Idees de selection heuristique depuis `scene_verifier.agent`.
- Telemetry.
- Serialization JSON.

## A ne pas recuperer tel quel

- Correction directe de prose finale.
- Logique qui depend de `scene_descriptions.json`.

## Taches

1. Creer `BlackboardController`.
2. Ingerer les `EvidenceAnswer` des agents.
3. Valider les invariants:
   - support obligatoire pour `supported`;
   - confidence coherente;
   - final state timestamped;
   - do-not-claim explicite pour les faits rejetes.
4. Dedoublonner:
   - par similarite lexicale;
   - par overlap temporel;
   - par claim type;
   - par personnages.
5. Detecter conflits:
   - contradictions temporelles;
   - etats finaux incompatibles;
   - claims critiques concurrents.
6. Produire:
   - `blackboard.json`;
   - `conflicts.json`;
   - `do_not_claim_list`.

## Criteres de validation

- Le composer ne recoit que des facts supportes ou explicitement utilisables.
- Les facts incertains et rejetes sont conserves mais marques.
- Les contradictions critiques sont remontees a `ArbitrationPanel`.
