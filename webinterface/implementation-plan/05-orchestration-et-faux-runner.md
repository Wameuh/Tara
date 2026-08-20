# Etape 05 - Orchestration et faux runner

## Objectif

Construire la file FIFO, le process pool, les machines de jobs et un faux runner contractuel afin de tester tout le produit avant l'integration de Tara reel.

## Dependances

Etapes 02 et 03.

## Fichiers a creer ou modifier

- `src/tara_web/orchestration/scheduler.py`.
- `src/tara_web/orchestration/process_pool.py`.
- `src/tara_web/orchestration/ipc.py`.
- `src/tara_web/orchestration/job_service.py`.
- `src/tara_web/orchestration/cancellation.py`.
- `src/tara_web/runners/fake.py`.
- `src/tara_web/runners/factory.py`.
- `tests/web/orchestration/`.

## Ordonnancement

- Une seule instance FastAPI possede SQLite, l'ordonnanceur et le pool.
- La file est FIFO et reconstruite depuis SQLite au demarrage.
- `max_active_jobs` vaut 5 par defaut; 25 jobs peuvent attendre.
- Le scheduler revendique un job par transition conditionnelle, cree un snapshot immuable, puis le soumet au pool.
- Les workers n'ecrivent jamais dans SQLite. Ils renvoient les evenements et le resultat par une file IPC serialisable.
- Le processus principal applique les evenements, incremente la revision et notifie les abonnes.

Sous pression IPC, coalescer uniquement `stage_progress` par job et etape. Ne jamais perdre changement d'etape, warning important, retry, usage, annulation ou fin.

## Faux runner

Le faux runner doit:

- parcourir toutes les etapes metier dans le bon ordre;
- produire les deux pourcentages et des sous-etapes plausibles;
- generer un YAML public conforme au schema cible;
- accepter des modes deterministes: succes, echec, timeout, retry et blocage annulable;
- declarer des artefacts realistes sans contenu utilisateur sensible;
- ne jamais inserer de ligne dans l'historique d'estimation reel.

Ses durees sont courtes et configurables pour les tests, mais le comportement de revision et d'evenements est identique au runner reel.

## Annulation et redemarrage

Une annulation suit `cancel_requested -> stopping -> cancelled|cancel_failed`. Le token cooperatif est visible du worker; un appel non interruptible peut finir mais son resultat est ignore et nettoye. Un job en attente est annule sans demarrer.

Au redemarrage, les jobs actifs sans worker deviennent `failed` avec le code d'interruption serveur et restent relancables. Les jobs en attente reprennent leur ordre FIFO.

## Securite

- Lancer les workers avec un compte/jeton de moindre privilege, un environnement reduit et uniquement les chemins du job courant; ne jamais leur transmettre le secret proprietaire.
- Valider strictement type, taille, version et identifiant de tout message IPC avant mutation SQLite; un message inconnu ou provenant d'un job non revendique est rejete.
- Borner file IPC, nombre de processus, memoire, temps CPU et duree par job; tuer puis remplacer un worker qui depasse les limites apres la grace d'annulation.
- Dimensionner la limite Docker de PIDs pour le serveur, le process pool, FFmpeg et la marge d'arret; une valeur trop basse doit echouer au preflight plutot qu'en plein job.
- Ne jamais deserialiser IPC avec `pickle` depuis une source non totalement maitrisee; preferer primitives JSON/msgpack validees ou dataclasses converties explicitement.
- Empecher qu'un faux runner soit active en production par simple valeur client; le mode est une configuration serveur verifiee au demarrage.
- Proteger les transitions terminales contre les doubles fins, evenements tardifs et confusion d'identifiant de job.

## Validation

- Tests de concurrence avec 5 actifs et 25 en attente.
- Aucun sixieme job actif, meme sous appels simultanes.
- Reconstruction FIFO exacte apres redemarrage.
- Annulation testee dans chaque etape et juste avant la fin.
- Saturation IPC simulant des milliers de progressions sans perte d'evenement critique.
- Le YAML du faux runner passe la validation publique et sa promotion atomique.
- Des tests injectent messages IPC mal formes, surdimensionnes, tardifs et attribues au mauvais job sans corruption d'etat.
- Les tests de file, annulation et redemarrage passent dans le conteneur avec l'init minimal et les limites PIDs/memoire retenues.

## Definition de fin

Le backend execute un parcours complet et observable avec le faux runner, y compris erreur, timeout, annulation et crash serveur, sans dependance a une inference reelle.
