# 10 - AdversarialAuditAgent et FinalPatchAgent

## Objectif

Verifier le draft final de maniere ciblee, puis corriger sans ajouter de facts.

## A recuperer de Tara

- Idee de statut `SUPPORTED`, `PARTIAL`, `UNSUPPORTED` depuis `scene_verifier`.
- Rapport de verification comme artefact inspectable.
- Telemetry d'appels LLM.

## A ne pas recuperer tel quel

- Verification de tous les claims par plusieurs appels couteux.
- Correction qui reecrit largement sans controle de support.

## Taches

1. Implementer `AdversarialAuditAgent`.
2. Cibles d'audit:
   - phrase sans support;
   - claim critique;
   - claim base sur fact partial;
   - claim proche d'une incertitude;
   - claim qui combine trop de faits;
   - fuite de do-not-claim.
3. Produire `audit_report.json`.
4. Si probleme:
   - demander retrieval cible si preuve manquante;
   - retrograder un claim;
   - exiger suppression;
   - proposer patch source.
5. Implementer `FinalPatchAgent`.
6. Le patch agent recoit seulement:
   - draft;
   - corrections autorisees;
   - claims supportes;
   - claims interdits.
7. Repasser l'audit apres patch.

## Criteres de validation

- `forbidden_claim_leak_count` vaut 0.
- Les claims critiques non supportes sont supprimes ou marques non confirmes.
- Aucun patch n'ajoute de fait absent de la blackboard.
