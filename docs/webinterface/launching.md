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

Le reverse proxy TLS publie `127.0.0.1:8443` par défaut. Le tableau de bord
d'administration publie séparément `127.0.0.1:8765`, sans TLS, et n'est jamais
routé par le proxy public. Les deux processus FastAPI restent isolés dans leurs
réseaux respectifs. Les volumes séparés conservent SQLite, les jobs et les
sauvegardes ; la configuration et les secrets sont montés en lecture seule.

Ouvrir <http://127.0.0.1:8765> depuis la machine hôte pour consulter les vues
agrégées, les états des analyses, les dernières réceptions Ko-fi et la
consommation mensuelle. Le formulaire `+ Ajouter` / `− Retirer` crée une
correction signée et auditée pour le mois courant. Le cumul public vaut
`max(0, estimation provider + corrections du mois)`. Le port peut être changé
avec `TARA_ADMIN_HOST_PORT`, mais l'adresse de publication reste volontairement
fixée à `127.0.0.1` et ne doit pas être transférée par le routeur.

Arrêter la pile sans supprimer les volumes :

```bash
docker compose -f compose.yaml -f compose.override.yaml down
```

## Construire et mettre à jour le serveur live de cette machine

L'instance actuellement exposée utilise la surcharge privée
`$TARA_LIVE_OVERRIDE`. Elle monte les
certificats, les credentials provider, l'authentification Cursor, le token
Ko-fi et la configuration web locale sans placer leur contenu dans Git ni dans
l'image. Les commandes suivantes sont à exécuter depuis la racine de
`TaraRepo` dans le même terminal :

```bash
export TARA_LIVE_OVERRIDE="${TARA_LIVE_OVERRIDE:?set it to the private Compose override path}"
TARA_LIVE_DIR="$(dirname -- "$TARA_LIVE_OVERRIDE")"

export TARA_WEB_BIND_ADDRESS=0.0.0.0

git status --short
git rev-parse --short HEAD
df -h /
sg docker -c 'docker system df'
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" config --quiet'
export TARA_PREVIOUS_IMAGE="$(sg docker -c 'docker image inspect tara-web:local --format "{{.Id}}"')"
TARA_OCI_REVISION="$(git rev-parse HEAD)" \
  sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" build --pull tara-web-init'
```

La construction exécute ESLint, Vitest, TypeScript, le build Vite, le budget du
bundle ainsi que les contrôles OpenAPI et i18n. Elle ne modifie pas encore les
conteneurs live. Si elle échoue, conserver l'ancienne instance et corriger le
checkout avant toute bascule.

Conserver au moins 6 Go libres avant une reconstruction complète : l'image
contient notamment FFmpeg, Python, Node et les dépendances frontend. Si le
cache de construction inutilisé occupe l'espace disponible, le nettoyer avant
le build avec la commande suivante. Elle ne supprime ni image taguée, ni
conteneur, ni volume persistant, mais le prochain build réutilisera moins de
couches :

```bash
sg docker -c 'docker builder prune --all --force'
df -h /
```

Un disque plein peut faire échouer SQLite avec `disk I/O error`. Dans ce cas,
ne pas restaurer ni supprimer la base : libérer le cache Docker, vérifier que
plusieurs gigaoctets sont de nouveau disponibles, puis relancer la sauvegarde
et la pile.

Après une construction réussie, arrêter proprement l'application, créer une
sauvegarde authentifiée, appliquer automatiquement les migrations et démarrer
la nouvelle image :

```bash
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" stop tara-web tara-admin'
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" --profile operations run --rm tara-web-backup'
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" up -d --no-build'
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" ps'
```

Valider ensuite TLS et les deux niveaux de santé, puis contrôler Ko-fi et les
journaux récents. `--resolve` teste le certificat public contre le service
local sans dépendre du routage NAT du réseau :

```bash
curl --fail --silent --show-error \
  --cacert "$TARA_LIVE_DIR/secrets/tls.crt" \
  --resolve tara-wameuh.duckdns.org:8443:127.0.0.1 \
  https://tara-wameuh.duckdns.org:8443/api/v1/live
curl --fail --silent --show-error \
  --cacert "$TARA_LIVE_DIR/secrets/tls.crt" \
  --resolve tara-wameuh.duckdns.org:8443:127.0.0.1 \
  https://tara-wameuh.duckdns.org:8443/api/v1/ready
curl --fail --silent --show-error \
  --cacert "$TARA_LIVE_DIR/secrets/tls.crt" \
  --resolve tara-wameuh.duckdns.org:8443:127.0.0.1 \
  https://tara-wameuh.duckdns.org:8443/api/v1/funding/monthly
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" logs --tail 200 tara-web'
```

Pour ne voir que les réceptions ou rejets Ko-fi :

```bash
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" logs tara-web' \
  | grep 'kofi_webhook_'
```

Si la nouvelle application ne devient pas saine et qu'aucune migration
incompatible n'a été appliquée, remettre l'image précédente puis recréer les
services :

```bash
sg docker -c 'docker image tag "$TARA_PREVIOUS_IMAGE" tara-web:local'
sg docker -c 'docker compose -f compose.yaml -f "$TARA_LIVE_OVERRIDE" up -d --no-build'
```

Après une migration incompatible avec l'ancienne image, ne pas tenter de
rétrograder SQLite en place : suivre la procédure de restauration dans un
volume neuf décrite par le [runbook](runbook.md). Ne jamais utiliser `down -v`,
qui supprimerait les volumes persistants.

## Vérification locale

Le smoke test construit une pile éphémère, vérifie TLS, santé, isolation,
privilèges, arrêt contrôlé, sauvegarde et restauration, puis supprime ses
propres volumes :

```bash
scripts/smoke-web-compose.sh
```

Sous Docker Desktop Windows, `scripts/smoke_web_compose.ps1` délègue le même
scénario à Bash via WSL ou Git Bash. La matrice complète et les contrôles de
livraison sont détaillés dans [Validation V1 web](mvp-validation.md). Pour une
instance durable, utiliser le [runbook](runbook.md), le [guide API](api.md) et
la [checklist de livraison](release-checklist.md).

## Galerie des vues sans backend

Les états principaux du frontend peuvent être inspectés avec des contenus
Lorem Ipsum sans lancer Tara ni Docker :

```bash
python -m http.server 8090 --directory webinterface
```

Ouvrir ensuite <http://127.0.0.1:8090/view-examples/>. Cette galerie statique
réutilise les styles de production mais ne fait partie d'aucun bundle livré. Sa
couverture et ses limites sont documentées dans
[`webinterface/view-examples/README.md`](../../webinterface/view-examples/README.md).
