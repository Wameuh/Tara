# Etape 11 - Providers, Modal et telemetrie par tentative

## Objectif

Rendre les appels de transcription et d'inference compatibles avec de gros audios, une concurrence bornee, le calcul de cout et la comptabilisation des tentatives reussies ou echouees.

## Dependances

Etape 10.

## Fichiers a creer ou modifier

- `src/tara/modal_transcription.py`.
- `src/tara/run_reporting.py`.
- `src/tara/analysis/llm_runner.py` et appels providers.
- `src/tara/providers/events.py`.
- `src/tara/providers/costing.py`.
- tests Modal/HTTP et fixtures de tentatives.

## Transport et concurrence Modal

Supprimer l'hypothese selon laquelle tous les fichiers tiennent en memoire et sous la limite inline. Mettre en place un transport borne adapte au provider: stockage objet/volume temporaire avec reference securisee, ou upload en flux si l'API le permet.

L'ordonnanceur par job maintient au plus `transcription_parallelism` appels soumis. Algorithme conseille:

1. file locale des pistes non soumises;
2. remplir jusqu'a la limite;
3. traiter le premier futur termine;
4. emettre usage/progression et liberer les ressources de transport;
5. soumettre la piste suivante;
6. en annulation, ne plus soumettre et annuler ce qui peut l'etre.

Ne jamais construire une liste contenant les octets de toutes les pistes. Une piste de 5h et un nombre non fixe de pistes doivent etre traitables dans les limites configurees de disque/memoire.

## Telemetrie par tentative

Chaque appel provider emet un `UsageAttempt` durable avec job, tentative, famille d'operation, modele, timestamps, statut, tokens d'entree/sortie/cache, duree, devise/source de prix et cout disponible. L'evenement est emis dans un bloc `finally` afin de compter les echecs ayant consomme.

Les erreurs textuelles et reponses completes ne sont pas stockees dans la telemetrie. Un identifiant de tentative relie retries et resultat final sans exposer le provider au frontend.

## Cout Modal

Les buckets globaux de facturation qui se chevauchent ne doivent pas etre attribues directement a un job concurrent. Produire d'abord une estimation par job a partir des unites mesurables de l'appel, puis permettre une reconciliation d'exploitation separee. Documenter clairement ce qui est mesure, estime ou indisponible.

## Securite

- Utiliser des references de transport temporaires, non devinables, limitees au fichier, en lecture seule et a courte expiration; les revoquer et nettoyer apres consommation.
- Verifier TLS, hostname et delais des endpoints providers; interdire toute URL provider ou destination choisie par l'utilisateur afin d'eviter SSRF.
- Isoler credentials par provider/famille, appliquer moindre privilege et rotation; ne jamais les mettre dans arguments de processus, logs ou messages IPC.
- Borner reponse provider, nombre de segments, tokens et temps; refuser une reponse trop grande ou mal formee avant de l'integrer au pipeline.
- Signer ou authentifier les callbacks entrants s'il en existe, proteger contre replay et lier chaque reponse a la tentative/job attendu.
- La telemetrie utilise une liste positive de champs et des identifiants pseudonymes; aucun audio, texte, prompt ou message d'erreur brut n'y est conserve.

## Validation

- Test avec fichiers factices depassant la limite inline sans chargement global en memoire.
- Mesure du pic memoire independante du nombre total de pistes.
- Respect strict du parallelisme configure.
- Tentative reussie, echouee, annulee et timeout toutes enregistrees avec donnees disponibles.
- Retries Tara non doubles par l'orchestrateur web.
- Sommes de cout reproductibles et absence d'attribution croisee entre jobs concurrents.
- Tests SSRF, reference expiree, callback rejoue, reponse geante et credential place dans une exception sans fuite persistante.

## Definition de fin

Les providers emettent une telemetrie exploitable par job et traitent les audios longs avec transport et concurrence bornes.

## Cloture de la tache

- Le transport Modal ne serialise plus les octets audio dans les jobs. Chaque piste est verifiee, hachee en flux puis deposee dans un volume temporaire sous une reference aleatoire liee au run, avec taille, SHA-256 et expiration controles a nouveau dans le conteneur GPU.
- L'ordonnanceur Modal maintient une fenetre stricte de soumissions par job. Il ne charge et ne depose une nouvelle piste que lorsqu'une place est libre, annule les appels actifs au mieux et nettoie les fichiers ainsi que le dictionnaire de progression en sortie normale ou exceptionnelle.
- Les reponses Modal sont bornees en octets, nombre de segments et taille de texte. Les chemins absolus, traversees de repertoire, references absentes, expirees, modifiees ou hors volume sont refuses avant ecriture locale.
- Les appels LLM, HTTP de transcription et Modal emettent un `UsageAttempt` ferme pour chaque tentative provider reelle, y compris echec, timeout et annulation apres consommation. Aucun message d'erreur, prompt, audio, transcript ou credential n'entre dans cet evenement.
- La migration SQLite 12 persiste les tentatives de facon idempotente et les rattache a la tentative de job par cle etrangere. Les agregats de tokens et de cout ne sont avances qu'une fois, meme en cas de callback rejoue ou d'identifiant duplique entre jobs.
- Le statut du cout est fige a la fin de chaque tentative de job: `complete` si toutes les consommations sont chiffrees, `partial` si un sous-ensemble seulement l'est, et `unavailable` sinon. Les relances conservent leurs propres tentatives et le resultat public additionne les tentatives connues sans masquer les couts rates.
- Les couts LLM et Modal conservent le montant natif, la devise, le taux de conversion et la source. Modal utilise une estimation par duree propre a l'appel et un tarif configurable; les buckets globaux qui se chevauchent restent un rapprochement d'exploitation et ne sont plus additionnes au cout d'un job.
- Le plafond audio de transport est configurable et vaut 1 Gio par defaut. Le test de fichier creux superieur a l'ancienne limite de 100 Mio confirme l'absence de `read_bytes`; le nombre de pistes n'augmente que les metadonnees et resultats, pas les octets audio residents.
- Les endpoints HTTP de provider restent issus de la configuration operateur. Aucun hote ou URL n'est accepte depuis les donnees utilisateur, ce qui maintient la protection SSRF et la politique de delais existantes.

Statut: signee le 2026-07-18 apres dix cycles de revue Terra Medium, corrections finales Codex et validation conteneurisee.

Validation finale:

- Ruff sur les modules et tests concernes: `All checks passed`.
- Tests cibles providers, Modal, persistance, API et contrats de l'etape 10: `186 passed, 6 skipped`.
- Suite Python complete: `776 passed, 10 skipped`.
- Frontend: lint, `41 passed`, build TypeScript/Vite, budget bundle, OpenAPI et i18n valides.
- Securite des dependances: `pip-audit` sans vulnerabilite connue et `npm audit --audit-level=high` sans vulnerabilite.
- Docker Compose reconstruit et demarre; `tara-web` est sain, `/api/v1/live` et `/api/v1/ready` repondent `200` en JSON via le proxy, avec CSP et `Referrer-Policy`.
