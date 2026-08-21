# Validation V1 web

Ce document définit les preuves requises avant une livraison V1. Les commandes
doivent partir d'un checkout propre avec les fichiers verrouillés. Les résultats
mesurés de la validation finale sont enregistrés dans la dernière section. Le
[rapport V1](v1-validation.md) distingue ces preuves reproductibles des actes
opérateur à signer pour une release donnée.

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
python scripts/smoke_real_audio_compose.py --playwright
```

Le test de charge initialise explicitement le tokenizer, admet 30 jobs sans
provider, revendique exactement 5 jobs actifs et conserve 25 jobs en file. Il
borne le p95 des lectures à 250 ms et chaque promotion à une seconde, puis
vérifie la reprise serveur et `PRAGMA integrity_check`.

La matrice Playwright couvre Chromium, Firefox, WebKit et Chromium mobile. Les
tests Axe vérifient WCAG A/AA ; les scénarios contrôlent aussi les largeurs
1440, 980, 390 et 320 px, le clavier, la réduction des animations et l'absence
de débordement horizontal.

Le premier smoke test Compose exerce TLS, santé, isolation, rootfs en lecture
seule, UID non privilégié, `SIGTERM`, sauvegarde et restauration. Un second
smoke provider-free démarre une inférence HTTP déterministe, envoie deux pistes
MP3/OGG par l'interface réelle dans Chromium, attend le résultat Tara et vérifie
l'attribution des personnes sans fuite de secret. La release génère ensuite des
SBOM SPDX JSON Python, npm et image, scanne les secrets et configurations des
sources suivies, puis scanne l'image avec Trivy verrouillé par digest. Un
rapport absent, un scan en erreur, ou une vulnérabilité corrigeable
`HIGH`/`CRITICAL` bloque la livraison.

## Sécurité des dépendances

La CI exporte les dépendances Python runtime depuis `uv.lock`, exécute
`pip-audit`, puis lance `npm audit --audit-level=high` sur le frontend. Les deux
rapports JSON sont conservés 14 jours et chaque audit est bloquant. Aucun
credential, média privé, base locale ou artefact d'analyse ne doit être ajouté
aux rapports ou au dépôt.

## Résultat de la validation finale

Validation locale exécutée le 2026-08-21 sur la révision `c4a5df1`, avant ce
seul ajout de preuve documentaire :

- Ruff : surface web maintenue sans erreur ;
- Pytest : **839 passed, 5 skipped**, 1 avertissement externe
  Starlette/httpx, en 177,66 s ; le test de charge 5+25 passe en 3,334 s ;
- frontend : ESLint, **53 tests Vitest**, TypeScript, build Vite, budget bundle,
  OpenAPI et manifestes i18n réussis ;
- Playwright : **60 passed, 4 skipped** en 4,7 min sur Chromium, Firefox,
  WebKit et Chromium mobile, contrôles Axe inclus ;
- `actionlint` et `docker compose config --quiet` réussis ;
- `pip-audit 2.10.1` : 45 dépendances runtime, **0 vulnérabilité connue** ;
- `npm audit --audit-level=high` : **0 vulnérabilité** ;
- smoke Compose complet réussi : TLS, santé, UID `10001:10001`, rootfs en
  lecture seule, absence de port FastAPI publié, `SIGTERM`, sauvegarde,
  restauration et `PRAGMA integrity_check` ;
- image reconstruite :
  `sha256:e433fe62949600a3a3d286379e48d62f8f58b05cf42ed925794e6ac421765d6d` ;
- SBOM SPDX JSON généré et Trivy 0.73.0 verrouillé par digest :
  **0 vulnérabilité corrigeable HIGH/CRITICAL**.

Les cinq skips Python sont attendus dans cet environnement : un probe Cursor
CLI réel explicitement opt-in et quatre validations média qui exigent FFmpeg
dans le conteneur de tests. FFmpeg est présent et contrôlé dans l'image de
production par le smoke Compose. Les quatre skips Playwright correspondent au
scénario qui exige `E2E_BASE_URL` vers une pile Compose réelle et qui est donc
exclu de la matrice statique ; le smoke Compose constitue une validation de
déploiement distincte. Enfin, le noyau de cet hôte ARM ne fournit pas les
contrôleurs cgroup mémoire à Docker ; Compose a donc
signalé que les limites mémoire locales étaient ignorées. La CI Ubuntu reste la
preuve de référence pour leur application, tandis que les autres contrôles de
durcissement ont réussi localement.
