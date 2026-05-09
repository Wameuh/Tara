# 08 - ArbitrationPanel

## Objectif

Trancher les contradictions avant que le resume final soit compose.

## A recuperer de Tara

- Idee de verification stricte depuis `scene_verifier`.
- Selection de candidats et fallback vers preuve brute, mais adaptes aux chunks de l'index.

## A ne pas recuperer tel quel

- Verification exhaustive claim-by-claim apres synthese.
- Correction narrative non sourcee.

## Taches

1. Definir politiques d'arbitrage:
   - mort/survie: evidence brute obligatoire;
   - position finale: timestamp le plus tardif supporte;
   - ennemi neutralise: neutralisation explicite obligatoire;
   - ressource: preferer depense/gain explicite;
   - ambigu: exclure ou marquer non confirme.
2. Lire `conflicts.json`.
3. Recuperer les chunks sources des claims en conflit.
4. Si besoin, lancer retrieval cible.
5. Si besoin critique, escalader vers LLM fort via `LLMRunner`.
6. Produire `arbitration_report.json`.
7. Mettre a jour la blackboard:
   - accepted;
   - rejected;
   - merged;
   - forbidden.

## Criteres de validation

- Un conflit critique ne passe jamais silencieusement au composer.
- Un claim interdit apparait dans `do_not_claim_list`.
- L'arbitrage documente la preuve utilisee.
- Le composeur n'arbitre pas lui-meme.
