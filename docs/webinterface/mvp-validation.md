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

umask 077
uv run pytest tests/web/security \
  tests/web/uploads/test_audio_probe.py \
  tests/web/uploads/test_merged_transcription_validation.py \
  tests/web/uploads/test_resumable_upload.py \
  tests/web/zip/test_zip_security.py \
  tests/web/api/test_sse_broker.py \
  tests/web/api/test_events_results.py \
  --junitxml=reports/web-adversarial.xml

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

Le garde adversarial dynamique démarre l'application avec `TestClient` et
réexécute les corpus négatifs HTTP, upload, média polyglotte, YAML hostile, ZIP
traversal/bomb et SSE borné. Il n'utilise aucune donnée réelle ni provider. Son
rapport JUnit distinct empêche qu'une régression de sécurité reste masquée dans
la suite fonctionnelle générale.

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

La configuration Compose rendue, les métadonnées d'image et leurs SHA-256 sont
archivés. Le digest OCI local reçoit une provenance SLSA signée avec l'identité
OIDC du workflow par `actions/attest` verrouillé sur un commit immuable ; l'image
reste locale et n'est jamais poussée par cette vérification.

## Sécurité des dépendances

La CI exporte les dépendances Python runtime depuis `uv.lock`, exécute
`pip-audit`, puis lance `npm audit --audit-level=high` sur le frontend. Bandit
1.9.4 analyse également tout `src` et bloque toute alerte SAST de sévérité
élevée avec une confiance moyenne ou élevée. Les trois rapports JSON sont
conservés 14 jours et chaque contrôle est bloquant. Aucun credential, média
privé, base locale ou artefact d'analyse ne doit être ajouté aux rapports ou au
dépôt.

## Résultat de la validation finale

Validation locale rejouée le 2026-08-21 sur la révision `00d2be5`, avant ce
seul ajout de preuve documentaire :

- Ruff sur la surface web maintenue, Actionlint et modèle Compose : réussis ;
- Pytest avec tous les extras : **845 passed, 5 skipped**, 1 avertissement
  externe Starlette/httpx, en 174,23 s ; le test de charge 5+25 est inclus ;
- garde adversarial API/upload/YAML/ZIP/SSE : **74 passed, 2 skipped** en
  56,51 s ;
- frontend : ESLint, **53 tests Vitest**, TypeScript, build Vite, budget bundle
  et contrat OpenAPI réussis ;
- Playwright : **60 passed, 4 skipped** en 4,6 min sur Chromium, Firefox,
  WebKit et Chromium mobile, contrôles Axe inclus ;
- parcours Compose réel distinct : **1 passed** dans Chromium, puis smoke API
  MP3/OGG rejoué avec succès sur l'image finale ;
- `pip-audit 2.10.1` : **0 vulnérabilité connue** ;
- `npm audit --audit-level=high` : **0 vulnérabilité** sur 344 dépendances ;
- Bandit 1.9.4 : **0 finding HIGH** ; le scan exhaustif non bloquant conserve
  10 heuristiques MEDIUM sans suppression dans le code ;
- deux SBOM source SPDX générés, scan secrets/configuration des fichiers suivis
  réussi et manifeste SHA-256 vérifié ;
- smoke Compose complet réussi : TLS, santé, UID `10001:10001`, rootfs en
  lecture seule, absence de port FastAPI publié, `SIGTERM`, sauvegarde,
  restauration et `PRAGMA integrity_check` ;
- image reconstruite :
  `sha256:091663a4b7e5390fe2bd6bae582191091e0e1c26cf6d1f91bffce104f66acd7e` ;
- SBOM SPDX JSON généré et Trivy 0.73.0 verrouillé par digest :
  **0 vulnérabilité corrigeable HIGH/CRITICAL**.

Les cinq skips Python sont attendus dans cet environnement : un probe Cursor
CLI réel explicitement opt-in et quatre validations média qui exigent FFmpeg
dans le conteneur de tests. FFmpeg est présent et contrôlé dans l'image de
production par le smoke Compose. Les deux skips du garde adversarial ont la
même cause. Les quatre skips Playwright correspondent au scénario qui exige
`E2E_BASE_URL` vers une pile Compose réelle ; ce scénario passe séparément dans
Chromium contre la pile TLS réelle. Enfin, le noyau de cet hôte ARM ne fournit
pas les contrôleurs cgroup mémoire à Docker ; Compose a donc signalé que les
limites mémoire locales étaient ignorées. La CI Ubuntu reste la preuve de
référence pour leur application, tandis que les autres contrôles de durcissement
ont réussi localement.

Trois preuves exigent encore un environnement externe et ne sont donc pas
présentées comme exécutées localement : l'émission OIDC de l'attestation signée
par GitHub Actions, le test d'épuisement mémoire sur un hôte Linux avec cgroup
actif et le smoke Docker Desktop Windows. Elles restent bloquantes dans la
checklist de la release qui revendiquera ces environnements.
