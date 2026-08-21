# Lancer l'interface web

Docker Compose est le mode d'exploitation V1. Le lancement natif sert au
développement local et ne doit écouter que sur l'interface de bouclage.

## Développement natif

La CI de référence utilise Python 3.13.14, `uv` 0.8.0, Node.js 22.23.2 et les
fichiers de verrouillage du dépôt. Depuis la racine du dépôt :

```bash
uv sync --frozen --extra dev --extra deploy
uv run python scripts/build_web_brand_assets.py
cd webinterface/frontend
npm ci --ignore-scripts
npm run build
cd ../..
uv run tara-web --config config/webinterface.example.yaml
```

Sous PowerShell, les mêmes commandes s'appliquent ; utiliser `npm.cmd` si la
politique d'exécution bloque le lanceur PowerShell de npm.

L'exemple natif utilise le faux runner et écoute sur `127.0.0.1:8000`. Il ne
doit pas être exposé publiquement. Pour exercer le pipeline réel, copier la
configuration hors du dépôt, définir `runner_mode: tara` et fournir
`tara_config_path` vers une configuration Tara valide.

Les contrôles de santé sont disponibles sur `/api/v1/live` et
`/api/v1/ready`. `/api/v1/config/public` expose uniquement les capacités
publiques nécessaires au frontend. Les entrées V1 sont des pistes audio MP3 ou
OGG, une transcription fusionnée YAML conforme au schéma public, ou une archive
ZIP contenant les pistes audio.

Les surcharges d'exploitation admises sont `TARA_WEB_STORAGE_ROOT`,
`TARA_WEB_BACKUPS_ROOT`, `TARA_WEB_SQLITE_PATH`, `TARA_WEB_PUBLIC_URL`,
`TARA_WEB_ALLOWED_HOSTS` et `TARA_WEB_ALLOWED_ORIGINS`. Les secrets utilisent
les variables nommées dans la configuration ou des fichiers secrets Compose ;
ils ne doivent jamais être écrits dans le YAML versionné.

## Docker Compose

Préparer les certificats, clés et fichiers de surcharge comme décrit dans le
[guide de déploiement Docker](docker-deployment.md), puis valider et démarrer :

```bash
docker compose -f compose.yaml -f compose.override.yaml config --quiet
docker compose -f compose.yaml -f compose.override.yaml up --build -d
docker compose -f compose.yaml -f compose.override.yaml ps
```

Seul le reverse proxy TLS publie un port hôte, `127.0.0.1:8443` par défaut.
FastAPI reste sur le réseau interne. Les volumes séparés conservent SQLite, les
jobs et les sauvegardes ; la configuration et les secrets sont montés en lecture
seule.

Arrêter la pile sans supprimer les volumes :

```bash
docker compose -f compose.yaml -f compose.override.yaml down
```

## Vérification locale

Le smoke test construit une pile éphémère, vérifie TLS, santé, isolation,
privilèges, arrêt contrôlé, sauvegarde et restauration, puis supprime ses
propres volumes :

```bash
scripts/smoke-web-compose.sh
```

Sous Docker Desktop Windows, `scripts/smoke_web_compose.ps1` délègue le même
scénario à Bash via WSL ou Git Bash. La matrice complète et les contrôles de
livraison sont détaillés dans [Validation V1 web](mvp-validation.md).
