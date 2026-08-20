# Etape 17 - Deploiement Docker securise

## Objectif

Construire le mode de deploiement V1 obligatoire sous Docker Compose. Le conteneur doit limiter l'acces de Tara au serveur hote, fournir un environnement reproductible et respecter les contraintes des jobs longs, de SQLite, du process pool, des uploads et de SSE.

Le lancement natif reste un outil de developpement et de test local; il n'est pas un mode d'exploitation V1 supporte pour un service partage ou expose.

## Dependances

Etapes 01 a 16. L'image peut etre prototypee apres l'etape 01, mais sa validation finale exige les flux reels, la retention, les sauvegardes et l'arret controle.

## Fichiers a creer, finaliser ou modifier

- `Dockerfile`: faire evoluer le scaffold du MVP vers le build multi-stage frontend/backend et l'image runtime minimale de production.
- `.dockerignore`: exclusion du depot inutile, secrets, donnees, caches et artefacts locaux.
- `compose.yaml`: durcir la topologie MVP pour en faire la topologie de production de reference.
- `compose.override.yaml.example`: surcharges locales documentees sans secret.
- `docker/entrypoint.sh`: preflight puis lancement avec propagation correcte des signaux.
- `docker/healthcheck.py`: appel borne aux endpoints de sante sans dependance shell fragile.
- `docker/reverse-proxy/`: configuration Caddy ou Nginx versionnee.
- `docker/seccomp/` uniquement si un profil plus strict que le profil standard est valide et maintenable.
- `config/docker.example.yaml`: chemins internes et valeurs non sensibles.
- `scripts/docker-preflight.*`, `scripts/docker-migrate.*`, `scripts/docker-backup.*`.
- `tests/web/docker/`: smoke tests, isolation, signaux, volumes et migrations.
- `docs/webinterface/docker-deployment.md`: installation et exploitation.

## Topologie Compose

### Service `tara-web`

Un seul conteneur applicatif execute FastAPI, sert le build React, possede SQLite, l'ordonnanceur, le process pool et les taches periodiques. `replicas` reste a 1; aucun autoscaling ou lancement concurrent n'est supporte en V1.

Le port FastAPI est expose uniquement au reseau Compose interne. Il ne possede pas de mapping `0.0.0.0:port` dans le fichier de production.

### Service `reverse-proxy`

Le reverse proxy est le seul service publiant des ports sur l'hote. Il termine TLS, transmet vers `tara-web`, conserve le streaming SSE, desactive le buffering incompatible et applique des limites/delais coherents avec les chunks d'upload.

Les headers `Forwarded`/`X-Forwarded-*` sont recrees par le proxy; `tara-web` ne leur fait confiance que lorsque la connexion provient du reseau/service approuve. Les routes d'administration, OpenAPI et metriques privees ne sont pas publiees par defaut.

### Services optionnels

Un scanner antivirus ou serveur d'inference local utilise un service separe, un reseau interne et un volume d'echange minimal. Aucun port n'est publie sauf besoin d'exploitation documente. Les providers distants restent accessibles en sortie selon une politique pare-feu de l'hote.

## Construction de l'image

Le `Dockerfile` suit un build multi-stage:

1. etape Node epinglee: installation verrouillee, tests/build React;
2. etape Python de build: installation depuis `uv.lock`, wheels/cache sans credentials;
3. etape runtime minimale: Python, FFmpeg/ffprobe, certificats CA et bibliotheques strictement necessaires;
4. copie du package installe et du build frontend uniquement;
5. creation d'un utilisateur/groupe applicatif non-root avec UID/GID documentes;
6. ajout de labels OCI de version/source/revision et d'un SBOM genere en CI.

Aucun `.env`, depot Git, test, fixture audio, cache, cle provider ou secret de registre ne doit entrer dans le contexte final. Les images de base sont epinglees par version et, pour la livraison, par digest.

Le build frontend copie uniquement les derives web du logo references par Vite. Exclure `logo_animations.html`, les PNG sources non utilises et `tara-logo-no-yellow.png` de l'image runtime; conserver leur generation dans le stage de build ou en amont si necessaire.

## Volumes et chemins

Monter uniquement des chemins dedies:

- `tara-db`: SQLite, WAL et SHM;
- `tara-jobs`: uploads, work et resultats;
- `tara-backups`: destination de sauvegarde distincte;
- `tara-logs`: seulement si les logs ne vont pas vers stdout/stderr;
- configuration principale en lecture seule;
- secrets individuels en lecture seule via fichiers ou mecanisme de secrets de la plateforme.

Le depot, le home de l'operateur, `/`, `/var/run/docker.sock` et tout repertoire parent large sont interdits. SQLite et `tara-jobs` utilisent un stockage local au meme hote; aucun filesystem reseau. Le chiffrement au repos est fourni par le disque/volume hote et verifie par le preflight d'exploitation.

Les temporaires non persistants utilisent un `tmpfs` borne. Les fichiers qui doivent survivre a un redemarrage de conteneur restent dans `tara-jobs` et suivent la retention applicative.

## Durcissement du conteneur

Configuration minimale de `tara-web`:

- `user` non-root et aucune elevation disponible;
- `read_only: true`;
- `privileged: false`;
- `cap_drop: [ALL]`, sans ajout sauf justification testee;
- `security_opt: [no-new-privileges:true]` et profil seccomp standard;
- pas de partage des namespaces PID/IPC de l'hote;
- limites memoire, CPU et PIDs configurees;
- rotation et taille maximale des logs Docker;
- umask privee et permissions explicites des volumes;
- aucun secret dans variables visibles publiquement, labels, image ou arguments de build.

Si FFmpeg, SQLite ou le process pool requiert une exception, elle est documentee et limitee a la primitive necessaire; il est interdit de contourner le probleme avec `privileged: true` ou un montage hote large.

## Processus, signaux et jobs longs

Utiliser un init minimal (`init: true` ou equivalent) pour reap les processus enfants. Le serveur FastAPI doit etre PID enfant correctement signale, sans wrapper qui absorbe `SIGTERM`.

Sequence d'arret:

1. Compose envoie `SIGTERM`;
2. FastAPI passe en drain et devient non pret;
3. les jobs actifs disposent du delai de grace configure;
4. les workers recoivent l'annulation cooperative puis sont termines si necessaire;
5. reconciliation et sauvegarde d'arret s'executent;
6. le processus sort avant `stop_grace_period`.

Configurer `stop_grace_period` au-dessus du delai applicatif maximal. Ne pas utiliser une politique de redemarrage qui boucle rapidement sur un etat `unhealthy`; un redemarrage du conteneur interrompt les jobs actifs selon les regles deja documentees.

## Healthchecks et demarrage

- Le healthcheck de vie appelle `live` avec timeout court.
- La disponibilite `ready` est utilisee par le proxy/deploiement pour accepter le trafic.
- Le conteneur reste non pret pendant preflight, migration explicite requise ou reconciliation.
- Les dependances Compose utilisent des conditions de sante lorsqu'elles sont reellement necessaires, sans supposer que `depends_on` garantit la disponibilite metier.
- Un preflight verifie UID, permissions, volumes, espace disque, configuration, langues, FFmpeg et version SQLite avant le service public.

## Migrations, sauvegarde et restauration

Les migrations sont une commande Compose ponctuelle executee apres drain et sauvegarde, jamais automatiquement par plusieurs replicas. Elle monte uniquement la base, la configuration et la destination de sauvegarde necessaires.

Les commandes de sauvegarde/restauration utilisent des profils ou services one-shot distincts. La restauration ne partage pas simultanement le volume SQLite avec `tara-web`; Compose doit refuser ou la procedure doit verifier que le service applicatif est arrete.

## Securite reseau

- Seul le reverse proxy publie 80/443 ou le port explicitement choisi.
- `tara-web`, inference et scanner sont sur un reseau interne sans port hote.
- Les endpoints providers sont fixes par configuration approuvee; aucune URL utilisateur ne devient destination reseau.
- Le pare-feu hote limite les entrees aux ports publics et, lorsque possible, les sorties aux DNS/NTP/providers necessaires.
- Ne pas supposer que `internal: true` convient a `tara-web` si celui-ci doit joindre des providers Internet; separer les reseaux ou appliquer les regles hote adequates.
- Le proxy impose TLS moderne, taille de headers, timeouts, limites de connexions et conservation correcte de l'adresse de correlation sans exposer de secret.

## Algorithme de validation d'isolation

1. Construire l'image depuis un contexte propre et enregistrer son digest.
2. Inspecter image et Compose rendus pour detecter utilisateur root, privileges, capabilities, ports et montages interdits.
3. Demarrer sur des volumes temporaires avec secrets factices.
4. Depuis `tara-web`, tenter de lire le depot, le home hote, le socket Docker et un autre volume non monte; toutes les tentatives doivent echouer.
5. Verifier que seuls les chemins declares sont ecrivables et que la racine est en lecture seule.
6. Scanner ports hote et reseaux Compose; seul le proxy est joignable publiquement.
7. Executer upload, SSE, process pool, FFmpeg, resultat, retention, SIGTERM et redemarrage.
8. Executer scans de vulnerabilites et secrets puis produire SBOM/attestations.

## Validation

- `docker compose config` est valide et ne contient aucun secret en clair ou montage interdit.
- L'image tourne non-root, sans capability, sans privilege et avec root filesystem en lecture seule.
- Aucun acces au depot, home hote ou socket Docker n'est possible depuis `tara-web`.
- Seul le reverse proxy publie un port; FastAPI, inference et scanner restent internes.
- SQLite WAL, ecritures atomiques, process pool et FFmpeg fonctionnent sur les volumes montes.
- Upload reprenable et SSE fonctionnent derriere le proxy avec les limites et timeouts configures.
- `SIGTERM` declenche drain, reconciliation et sauvegarde avant la fin du `stop_grace_period`.
- Redemarrage, migration one-shot, sauvegarde et restauration sont testes sur une copie de donnees.
- Les limites memoire/PIDs provoquent un echec controle sans rendre l'hote indisponible.
- Le scan d'image ne contient aucune vulnerabilite critique ou elevee exploitable; le secret scanning et le SBOM sont archives avec la release.
- L'image runtime contient les logos optimises necessaires mais aucune galerie HTML ou source de travail de l'identite.
- Les scenarios sont verifies sur un hote Linux de production cible. Le developpement Docker Desktop Windows reste supporte, sans promettre une equivalence noyau parfaite.

## Definition de fin

Le service complet se deploie avec une commande Compose documentee, n'expose que le reverse proxy, ne peut ecrire que dans ses volumes dedies et passe les tests fonctionnels, d'isolation, de signaux, de migration et de restauration.

## Etat d'implementation (2026-08-20)

- Image multi-stage Node/Python avec contrôles frontend, OpenAPI et catalogues,
  runtime non-root minimal et labels OCI.
- Entrypoint à propagation directe des signaux, preflight fermé et healthcheck
  Python borné.
- Volumes DB, jobs et sauvegardes distincts, migration préalable et services
  one-shot de sauvegarde/restauration sans réseau.
- Reverse proxy TLS seul exposé, headers recréés et buffering SSE désactivé.
- Secrets montés par fichiers dans la surcharge, limites ressources/logs et
  documentation opérateur versionnées.
- Smoke test exécuté sur Docker Linux ARM64 : build verrouillé, migration
  réelle, TLS, santé, UID/rootfs/montages, arrêt `SIGTERM`, sauvegarde
  authentifiée, restauration isolée et intégrité SQLite validés.
- Scan Trivy après mise à jour d'`aiohttp` : aucune vulnérabilité HIGH/CRITICAL
  non corrigée et aucun secret détecté. SBOM SPDX 2.3 généré (340 paquets).
- Limite de l'hôte de validation : son noyau ignore les limites mémoire Docker.
  Le test d'épuisement contrôlé des ressources reste donc à rejouer sur l'hôte
  Linux de production doté du contrôleur mémoire cgroup.
