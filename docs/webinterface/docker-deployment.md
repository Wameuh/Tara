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

Ne montez jamais le dépôt, un home, `/`, ni `/var/run/docker.sock` dans les
conteneurs. SQLite, son WAL et les jobs doivent rester sur le même hôte, jamais
sur NFS/SMB.

## Préparation

Créer un répertoire `secrets/` non versionné avec des fichiers privés :

```text
secrets/tls.crt
secrets/tls.key
secrets/backup-signing-key
secrets/modal-token-id
secrets/modal-token-secret
```

Copier ensuite la surcharge sans y placer de valeur secrète :

```bash
cp compose.override.yaml.example compose.override.yaml
chmod 700 secrets
sudo chgrp 101 secrets/tls.crt secrets/tls.key
sudo chgrp 10001 secrets/backup-signing-key \
  secrets/modal-token-id secrets/modal-token-secret
chmod 640 secrets/*
export TARA_WEB_TLS_CERTIFICATE_FILE="$PWD/secrets/tls.crt"
export TARA_WEB_TLS_PRIVATE_KEY_FILE="$PWD/secrets/tls.key"
export TARA_WEB_BACKUP_KEY_FILE="$PWD/secrets/backup-signing-key"
export MODAL_TOKEN_ID_FILE="$PWD/secrets/modal-token-id"
export MODAL_TOKEN_SECRET_FILE="$PWD/secrets/modal-token-secret"
```

Compose monte les secrets locaux comme des fichiers liés et conserve leurs
propriétaires et permissions hôte. Les GID `101` (Nginx) et `10001` (Tara)
doivent donc avoir le droit de lecture ; le répertoire parent en mode `0700`
empêche les autres comptes hôte de les parcourir.

Valider avant chaque livraison :

```bash
docker compose -f compose.yaml -f compose.override.yaml config --quiet
docker compose -f compose.yaml -f compose.override.yaml build --pull
```

L'image doit également être scannée et accompagnée d'un SBOM dans la CI. Les
arguments `TARA_OCI_SOURCE`, `TARA_OCI_REVISION` et `TARA_OCI_VERSION` alimentent
les labels OCI sans introduire de secret dans l'image. Les bases Node, Python,
uv et Nginx sont verrouillées par version et digest ; leur mise à jour doit être
une modification explicite suivie d'un rebuild, des tests et du scan complet.

Sur l'hôte Linux cible, `scripts/smoke-web-compose.sh` construit une pile
éphémère, contrôle TLS, santé, isolation, rootfs, UID et `SIGTERM`, puis détruit
ses volumes. `scripts/docker-sbom.sh IMAGE /chemin/absolu/sbom.spdx.json` produit
le SBOM avec Syft. Sous Docker Desktop Windows, le script PowerShell délègue ce
même scénario à Bash (WSL ou Git Bash) afin de conserver une seule procédure de
référence.

## Démarrage et arrêt

```bash
docker compose -f compose.yaml -f compose.override.yaml up -d
docker compose -f compose.yaml -f compose.override.yaml ps
```

Seul `tara-proxy` publie le port HTTPS 8443, limité à `127.0.0.1` par défaut.
Définir explicitement `TARA_WEB_BIND_ADDRESS=0.0.0.0` uniquement si le pare-feu
hôte et le certificat sont prêts. FastAPI n'a aucun port hôte.

`tara-web-init` fixe les propriétaires des trois volumes. `tara-web-migrate`
s'exécute une seule fois avant l'application. L'entrypoint applicatif impose un
umask privé, charge les secrets montés, vérifie UID, permissions, espace libre,
SQLite, FFmpeg, configuration et catalogues, puis utilise `exec` afin que
`SIGTERM` atteigne Uvicorn.

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web
```

Le service devient non prêt, draine les jobs, réconcilie et sauvegarde avant la
fin des 90 secondes de `stop_grace_period`.

## Sauvegarde ponctuelle

Arrêter ou drainer l'application avant une opération hors arrêt contrôlé :

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web
docker compose -f compose.yaml -f compose.override.yaml \
  --profile operations run --rm tara-web-backup
```

Le service n'a pas de réseau et ne monte que configuration, DB, jobs et volume
de sauvegarde. Les audios et intermédiaires ne sont pas copiés.

## Restauration

La restauration est volontairement isolée dans un nouveau volume et refuse une
racine déjà existante. L'application doit rester arrêtée :

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web
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
