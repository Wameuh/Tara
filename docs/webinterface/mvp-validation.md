# Validation V1 web

Ce document définit les preuves requises avant une livraison V1. Les commandes
doivent partir d'un checkout propre avec les fichiers verrouillés. Les résultats
mesurés de la validation finale sont enregistrés dans la dernière section.

## Contrats fonctionnels couverts

- entrées audio MP3/OGG, transcription fusionnée YAML et archive ZIP ;
- pipeline Tara réel en production, faux runner limité aux tests sans provider ;
- upload reprenable, validation, file FIFO 5+25, progression SSE et polling ;
- annulation, relance, expiration, reprise après crash et rotation du secret ;
- résultat navigable, recherche, accordions, copie et informations de coût ;
- arrêt contrôlé, réconciliation, sauvegarde authentifiée et restauration ;
- contrats publics de capacités et d'aide, français et anglais.

Les tests d'intégration du runner réel remplacent les providers externes par des
doubles déterministes. Ils valident l'adaptateur, les artefacts et les
transitions sans effectuer d'inférence payante ou dépendre d'un service distant.

## Contrôles CI

La CI utilise Python 3.13.14, `uv` 0.8.0 et Node.js 22.23.2. Les commandes de
référence sont :

```bash
uv sync --frozen --all-extras
uv run ruff check src/tara_web tests/web/test_ci_static.py \
  scripts/generate_web_openapi.py scripts/generate_i18n_manifest.py \
  scripts/docker_preflight.py docker/healthcheck.py
umask 077
uv run pytest --junitxml=reports/python-tests.xml

cd webinterface/frontend
npm ci --ignore-scripts
npm run check
npm run test:e2e -- --project=chromium
```

`npm run check` enchaîne lint, Vitest, TypeScript, build Vite, budget du bundle,
contrat OpenAPI et manifestes i18n. Le job conteneur valide également le modèle
Compose et reconstruit l'image de production.

## Contrôles de release

La vérification de release est déclenchée manuellement ou par une étiquette
`v*`. Elle n'effectue aucun push d'image :

```bash
uv sync --frozen --extra dev
uv run pytest tests/web/load/test_mvp_load.py \
  --junitxml=reports/web-load.xml --durations=1

cd webinterface/frontend
npm ci --ignore-scripts
npm run test:e2e

cd ../..
docker compose config --quiet
scripts/smoke-web-compose.sh
```

Le test de charge initialise explicitement le tokenizer, admet 30 jobs sans
provider, revendique exactement 5 jobs actifs et conserve 25 jobs en file. Il
borne le p95 des lectures à 250 ms et chaque promotion à une seconde, puis
vérifie la reprise serveur et `PRAGMA integrity_check`.

La matrice Playwright couvre Chromium, Firefox, WebKit et Chromium mobile. Les
tests Axe vérifient WCAG A/AA ; les scénarios contrôlent aussi les largeurs
1440, 980, 390 et 320 px, le clavier, la réduction des animations et l'absence
de débordement horizontal.

Le smoke test Compose exerce TLS, santé, isolation, rootfs en lecture seule,
UID non privilégié, `SIGTERM`, sauvegarde et restauration. La release génère
ensuite un SBOM SPDX JSON et scanne l'image avec Trivy verrouillé par digest. Un
scan absent, en erreur, ou une vulnérabilité corrigeable `HIGH`/`CRITICAL`
bloquent la livraison.

## Sécurité des dépendances

La CI exporte les dépendances Python runtime depuis `uv.lock`, exécute
`pip-audit`, puis lance `npm audit --audit-level=high` sur le frontend. Les deux
rapports JSON sont conservés 14 jours et chaque audit est bloquant. Aucun
credential, média privé, base locale ou artefact d'analyse ne doit être ajouté
aux rapports ou au dépôt.

## Résultat de la validation finale

À renseigner après l'exécution complète de la tâche de validation finale : date,
révision, décomptes Python/Vitest/Playwright, charge, audits, smoke Compose,
digest de l'image et résultat du scan.
