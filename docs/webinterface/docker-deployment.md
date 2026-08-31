# Déploiement Docker Compose

Docker Compose est le mode d'exploitation V1 supporté. Le lancement natif est
réservé au développement local. La topologie contient un initialiseur de
volumes, une migration ponctuelle, l'application, le reverse proxy TLS et deux
services d'exploitation sous profils (`backup` et `restore`).

## Prérequis

- hôte Linux avec Docker Engine et Compose v2 récents ;
- stockage local chiffré au repos pour les volumes DB, jobs et sauvegardes ;
- certificat TLS et clé privée lisibles par Docker ;
- clé de signature de sauvegarde aléatoire d'au moins 32 octets ;
- limites CPU, mémoire et PID adaptées à FFmpeg et au nombre de workers.
- AppArmor 4 actif sur l'hôte pour isoler Cursor CLI.

Ne montez jamais le dépôt, un home, `/`, ni `/var/run/docker.sock` dans les
conteneurs. SQLite, son WAL et les jobs doivent rester sur le même hôte, jamais
sur NFS/SMB.

## Préparation

Créer un répertoire `secrets/` non versionné avec des fichiers privés.
Pour le mode HTTP `modal_proxy`, utilisé par l'instance live, préparer :

```text
secrets/tls.crt
secrets/tls.key
secrets/backup-signing-key
secrets/modal-proxy-auth-key
secrets/modal-proxy-auth-secret
secrets/kofi-verification-token
secrets/cursor-auth.json
```

Copier ensuite la surcharge sans y placer de valeur secrète :

```bash
cp compose.override.yaml.example compose.override.yaml
chmod 700 secrets
sudo chgrp 101 secrets/tls.crt secrets/tls.key
sudo chgrp 10001 secrets/backup-signing-key \
  secrets/modal-proxy-auth-key secrets/modal-proxy-auth-secret \
  secrets/kofi-verification-token \
  secrets/cursor-auth.json
chmod 640 secrets/*
export TARA_WEB_TLS_CERTIFICATE_FILE="$PWD/secrets/tls.crt"
export TARA_WEB_TLS_PRIVATE_KEY_FILE="$PWD/secrets/tls.key"
export TARA_WEB_BACKUP_KEY_FILE="$PWD/secrets/backup-signing-key"
export TARA_MODAL_PROXY_AUTH_KEY_FILE="$PWD/secrets/modal-proxy-auth-key"
export TARA_MODAL_PROXY_AUTH_SECRET_FILE="$PWD/secrets/modal-proxy-auth-secret"
export TARA_KOFI_VERIFICATION_TOKEN_FILE="$PWD/secrets/kofi-verification-token"
export TARA_CURSOR_AUTH_FILE="$PWD/secrets/cursor-auth.json"
export TARA_CURSOR_AGENT_DIR="$HOME/.local/share/cursor-agent/versions/VERSION"
```

Les deux familles de credentials Modal ne sont pas interchangeables :

- `modal_proxy` exige une clé `wk-...` et un secret `ws-...` ;
- `modal_map` exige un identifiant API `ak-...` et un secret API `as-...`.

Ne monter que la paire correspondant à `TARA_INFERENCE_AUTH_PROVIDER`. Le
préflight refuse désormais le démarrage si la paire manque ou utilise les
mauvais préfixes. Pour `modal_map`, ajouter explicitement les fichiers
`modal-token-id` et `modal-token-secret` ainsi que leurs montages depuis
`compose.override.yaml.example` ; ne jamais les remplacer par des valeurs
aléatoires.

`TARA_CURSOR_AGENT_DIR` doit désigner le répertoire de version Linux de Cursor
Agent contenant `cursor-agent`, `node` et `index.js`. Copier le seul script
`cursor-agent` ne suffit pas. Copier le fichier d'authentification Cursor dans
`secrets/cursor-auth.json` sans en afficher le contenu ; l'entrypoint le
matérialise avec des permissions privées dans le `tmpfs` du conteneur. Le
pipeline web utilise Cursor CLI avec le modèle `Auto` par défaut.

Cursor utilise Bubblewrap dans l'image et un profil AppArmor dédié sur l'hôte.
Sur Debian, vérifier que `/sys/module/apparmor/parameters/enabled` vaut `Y` ;
sinon ajouter `apparmor=1 security=apparmor` à la ligne de commande du noyau et
redémarrer. Installer ensuite le profil versionné avant de créer le service :

```bash
sudo install -m 0644 docker/apparmor/tara-cursor-web \
  /etc/apparmor.d/tara-cursor-web
sudo apparmor_parser -r /etc/apparmor.d/tara-cursor-web
```

La surcharge Compose applique ce profil et
`docker/seccomp/tara-cursor-web.json` au seul service web. Le profil Seccomp
reprend le profil Moby et ajoute uniquement les appels de création/montage des
espaces de noms nécessaires à Bubblewrap ; AppArmor, le retrait des capacités,
le système de fichiers en lecture seule et `no-new-privileges` restent actifs.
La résolution DNS est forcée sur TCP parce que le profil AppArmor n'autorise
pas les sockets UDP. Le préflight refuse le démarrage si Cursor, Bubblewrap ou
le fichier d'authentification privé manque.

Compose monte les secrets locaux comme des fichiers liés et conserve leurs
propriétaires et permissions hôte. Les GID `101` (Nginx) et `10001` (Tara)
doivent donc avoir le droit de lecture ; le répertoire parent en mode `0700`
empêche les autres comptes hôte de les parcourir.

Valider avant chaque livraison :

```bash
docker compose -f compose.yaml -f compose.override.yaml config --quiet
docker compose -f compose.yaml -f compose.override.yaml build --pull
```

L'image et les sources doivent également être scannées et accompagnées de
SBOM. La vérification de release utilise une image Trivy verrouillée par version
et digest. Elle produit trois SBOM SPDX JSON (lock Python, lock npm et image),
scanne les secrets et configurations du seul contenu versionné, puis bloque les
vulnérabilités d'image corrigeables `HIGH` ou `CRITICAL`. Les quatre rapports
source portent un manifeste SHA-256. Tous les rapports sont conservés comme
artefacts CI pendant 14 jours. Les arguments
`TARA_OCI_SOURCE`, `TARA_OCI_REVISION` et `TARA_OCI_VERSION` alimentent les
labels OCI sans introduire de secret dans l'image. Les bases Node, Python, uv et
Nginx sont verrouillées par version et digest ; leur mise à jour doit être une
modification explicite suivie d'un rebuild, des tests et du scan complet.

Sur l'hôte Linux cible, `scripts/smoke-web-compose.sh` construit une pile
éphémère, contrôle TLS, santé, isolation, rootfs, UID et `SIGTERM`, puis détruit
ses volumes. Pour un contrôle opérateur indépendant,
`scripts/docker-sbom.sh IMAGE /chemin/absolu/sbom.spdx.json` produit un SBOM
local avec Syft. Sous Docker Desktop Windows, le script PowerShell délègue ce
même scénario à Bash (WSL ou Git Bash) afin de conserver une seule procédure de
référence.

La release archive aussi la configuration Compose rendue, les métadonnées et le
digest de configuration OCI de `tara-web:local`, puis `actions/attest` génère
une provenance SLSA signée par une identité OIDC GitHub éphémère. Le bundle
`image-provenance.json` se vérifie avec `gh attestation verify` contre le dépôt
qui a exécuté la release. Cette attestation n'effectue aucun push d'image.

Pour reproduire localement les rapports de source sans inclure les fichiers non
suivis du worktree :

```bash
scripts/source-security-reports.sh "$PWD/reports/source-security"
```

## Démarrage et arrêt

La procédure pas à pas pour reconstruire et mettre à jour l'instance live de
la machine de développement, avec sauvegarde, contrôles de santé et rollback,
se trouve dans [Lancer l'interface web](launching.md#construire-et-mettre-à-jour-le-serveur-live-de-cette-machine).

```bash
docker compose -f compose.yaml -f compose.override.yaml up -d
docker compose -f compose.yaml -f compose.override.yaml ps
```

`tara-proxy` publie le port HTTPS 8443, limité à `127.0.0.1` par défaut.
Définir explicitement `TARA_WEB_BIND_ADDRESS=0.0.0.0` uniquement si le pare-feu
hôte et le certificat sont prêts. `tara-admin` publie aussi son interface HTTP
sur `127.0.0.1:8765` par défaut. Le port hôte peut être changé avec
`TARA_ADMIN_HOST_PORT`. L'adresse de publication est volontairement fixée à la
boucle locale et l'application refuse les hôtes non locaux. Depuis une autre
machine, utiliser un tunnel SSH, par exemple
`ssh -L 8765:127.0.0.1:8765 utilisateur@serveur`, puis ouvrir
`http://127.0.0.1:8765`. Le réseau dédié de `tara-admin` n'est relié ni au
proxy, ni au réseau provider.

`tara-web-init` fixe les propriétaires des trois volumes. `tara-web-migrate`
s'exécute une seule fois avant l'application. L'entrypoint applicatif impose un
umask privé, charge les secrets montés, vérifie UID, permissions, espace libre,
SQLite, FFmpeg, configuration et catalogues, puis utilise `exec` afin que
`SIGTERM` atteigne Uvicorn.

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web tara-admin
```

Le service devient non prêt, draine les jobs, réconcilie et sauvegarde avant la
fin des 90 secondes de `stop_grace_period`.

## Sauvegarde ponctuelle

Arrêter ou drainer l'application avant une opération hors arrêt contrôlé :

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web tara-admin
docker compose -f compose.yaml -f compose.override.yaml \
  --profile operations run --rm tara-web-backup
```

Le service n'a pas de réseau et ne monte que configuration, DB, jobs et volume
de sauvegarde. Les audios et intermédiaires ne sont pas copiés.

## Restauration

La restauration est volontairement isolée dans un nouveau volume et refuse une
racine déjà existante. L'application doit rester arrêtée :

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web tara-admin
export TARA_RESTORE_GENERATION='backup-20260820T120000Z-0123456789abcdef0123456789abcdef'
docker compose -f compose.yaml -f compose.override.yaml \
  --profile restore run --rm tara-web-restore
```

Inspecter la racine restaurée et son audit avant de la promouvoir. Ne montez
jamais simultanément l'ancien volume DB et la copie restaurée dans `tara-web`.
La procédure cryptographique détaillée se trouve dans
[Sauvegarde et restauration](operations/backup-restore.md).

## Contrôles après déploiement

- `docker inspect` confirme UID `10001:10001`, rootfs en lecture seule,
  `CapDrop=ALL` et `no-new-privileges` ;
- aucun port n'est publié par `tara-web` ;
- `/api/v1/live` et `/api/v1/ready` sont sains derrière TLS ;
- SSE reste non bufferisé et un upload reprenable traverse le proxy ;
- le dépôt, le home hôte, le socket Docker et les volumes non montés sont
  inaccessibles ;
- un `SIGTERM` termine avant 90 secondes avec sauvegarde authentifiée ;
- le scan d'image et le SBOM sont archivés avec le digest livré.
