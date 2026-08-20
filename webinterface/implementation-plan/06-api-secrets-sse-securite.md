# Etape 06 - API, secrets, SSE et securite HTTP

## Objectif

Exposer le MVP par `/api/v1` avec snapshots REST canoniques, commandes idempotentes, lien proprietaire et suivi SSE securise.

## Dependances

Etapes 04 et 05.

## Fichiers a creer ou modifier

- `src/tara_web/api/routes/jobs.py`, `sessions.py`, `results.py`, `events.py`.
- `src/tara_web/api/dependencies/auth.py`.
- `src/tara_web/api/problem_details.py`.
- `src/tara_web/api/middleware/security.py`, `correlation.py`, `access_log.py`.
- `src/tara_web/realtime/broker.py`, `sse.py`.
- `tests/web/api/` et `tests/web/security/`.

## Contrat REST

Les snapshots de session et job contiennent la revision, le statut, les etapes, la progression, les estimations eventuelles, les entrees affichees, les avertissements publics, les expirations et `allowed_actions`. Les contenus volumineux utilisent des routes specialisees.

Pour alimenter `concept_design_1.html`, le snapshot job expose explicitement numero de tentative, langue, `started_at`, etapes ordonnees, sous-etape active, progression courante, progression totale, disponibilite/valeur de l'estimation, retention/expiration et actions autorisees. Chaque valeur absente reste nullable ou accompagnee d'un etat explicite; le frontend ne la reconstruit pas depuis des logs ou timestamps incomplets.

Pour alimenter `concept_design_2.html`, la route resultat renvoie le modele public type, les identifiants/ordres de sections, statut, expiration et un objet de cout approximatif `complete|partial|unavailable`. Elle ne renvoie ni YAML brut, ni chemin, ni telemetrie provider detaillee. Recherche, ouverture des sections et copie du texte restent locales au navigateur et ne creent aucun endpoint.

Les erreurs suivent RFC 9457 `application/problem+json` avec `type`, `title`, `status`, code Tara stable, identifiant de correlation et erreurs de champs facultatives. Aucun `str(exc)`, chemin ou nom de provider n'est expose.

## Secret proprietaire

- Generer un secret aleatoire d'au moins 256 bits.
- Retourner une seule fois l'URL `/jobs/{opaque_id}#secret=...`.
- Le frontend extrait le fragment, le garde uniquement en memoire de session et l'envoie dans `X-Tara-Job-Secret`.
- Le fragment n'atteint ni serveur, ni Referer, ni logs.
- Comparer les derivees en temps constant.
- La regeneration incremente la generation d'autorisation et invalide les anciens flux au prochain heartbeat.

Le lien donne tous les droits du job. L'API calcule les actions disponibles, mais chaque commande est revalidee. Les secrets invalides repetes sont limites par origine sans introduire de quota de creation de jobs par IP.

## SSE et fallback

Le client utilise `fetch` et parse le flux afin d'envoyer le header secret. Chaque evenement leger porte type, revision et donnees minimales. Un heartbeat configurable est un commentaire SSE sans donnee utilisateur.

Il n'existe pas de journal de replay persistant. Apres coupure, le client relit le snapshot REST, puis rouvre le flux. Une revision manquante, dupliquee ou plus ancienne declenche une resynchronisation. Des limites globales et par job peuvent refuser SSE; le polling continue alors.

## Securite HTTP

- CORS ferme par defaut et verification de l'origine pour les commandes navigateur.
- Validation de `Host` et confiance dans les headers proxy uniquement pour les proxies configures.
- CSP stricte, `frame-ancestors 'none'`, anti-sniffing, Referrer-Policy et Permissions-Policy.
- `no-store` pour ressources protegees; cache long seulement pour assets publics empreintes.
- HSTS uniquement lorsque HTTPS est determine de maniere fiable.
- Delais et tailles propres aux commandes, chunks et connexions longues.
- Journal d'acces structure avec route normalisee, statut, duree et correlation uniquement.
- En production Compose, accepter les headers proxy seulement depuis le reverse proxy interne et ne publier aucun port de `tara-web` sur l'hote.

## Securite applicative complementaire

- Appliquer des limites de debit distinctes aux secrets invalides, creations, commandes couteuses, polling et ouvertures SSE; une reponse de limitation ne confirme jamais l'existence d'un job.
- Rendre les erreurs d'identifiant absent, secret faux et job non accessible suffisamment uniformes pour reduire l'enumeration.
- Refuser les methodes et content types inattendus; imposer des tailles maximales aux JSON et headers avant parsing.
- Ne jamais refleter directement `Origin`, `Host`, nom de fichier ou detail d'erreur dans un header de reponse.
- Ajouter une protection contre request smuggling/desynchronisation au reverse proxy et documenter une seule chaine de confiance pour la taille du corps et le protocole.
- Tester les routes avec une matrice d'autorisation couvrant chaque statut et `allowed_action`, y compris les commandes rejouees et concurrentes.

## Validation

- OpenAPI genere et types TypeScript synchronises.
- Tests de contrat des snapshots `tracking` et `result` avec toutes les donnees requises par les deux concepts, ainsi que leurs etats absents/partiels.
- Tests d'acces sans secret, secret faux, ancienne generation et comparaison temporelle.
- Verification automatisee qu'aucun secret n'apparait dans URL serveur, logs ou Problem Details.
- Reconnexion SSE, fallback polling, heartbeat et limites de connexions testes.
- Tests Host, Origin, CORS, proxy de confiance, CSP et headers de cache.
- Fuzz leger des commandes et erreurs pour confirmer l'absence de fuite technique.
- Les tests de limitation et d'enumeration confirment qu'un attaquant ne peut ni saturer les flux SSE ni distinguer un identifiant valide sans secret.
- Les tests passent derriere le reverse proxy Compose et confirment SSE sans buffering, upload reprenable et impossibilite de joindre FastAPI directement depuis l'exterieur.

## Definition de fin

Un client peut creer, suivre, annuler, relancer et consulter un job uniquement via les contrats versionnes et son secret proprietaire.
