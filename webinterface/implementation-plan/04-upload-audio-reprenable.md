# Etape 04 - Upload audio reprenable

## Objectif

Implementer les sessions d'upload audio direct, reprises apres coupure, avec validation independante de chaque piste et association explicite a une personne.

## Dependances

Etapes 02 et 03.

## Fichiers a creer ou modifier

- `src/tara_web/api/routes/uploads.py`.
- `src/tara_web/services/upload_sessions.py`.
- `src/tara_web/services/chunk_upload.py`.
- `src/tara_web/services/input_validation.py`.
- `src/tara_web/services/audio_probe.py`.
- `src/tara_web/services/validation_scheduler.py`.
- `tests/web/uploads/` et fixtures MP3/OGG courtes.

## Ressources API

Prevoir des commandes idempotentes pour:

- creer ou reprendre une session;
- declarer un fichier avec taille totale et SHA-256 final;
- lire l'offset confirme;
- envoyer un chunk avec offset et SHA-256;
- finaliser, retenter, remplacer ou supprimer une piste;
- modifier la personne associee;
- annuler la session;
- obtenir un snapshot agrege.

Toutes les commandes exigent identifiant opaque, secret de session dans `X-Tara-Job-Secret`, cle d'idempotence si elles creent une ressource, et revision attendue si elles modifient le cycle de vie.

## Algorithme d'envoi d'un chunk

1. Verifier le secret, l'etat, l'offset attendu et la taille maximale.
2. Calculer SHA-256 en flux et comparer au header annonce.
3. Verrouiller logiquement le fichier par mise a jour conditionnelle d'offset.
4. Ecrire a l'offset confirme, vider et synchroniser.
5. Mettre a jour l'offset SQLite seulement apres persistance physique.
6. Renvoyer le nouvel offset canonique.

Apres redemarrage, tronquer les octets au-dela de l'offset confirme. Le frontend demande toujours l'offset serveur avant une reprise.

## Finalisation asynchrone

La finalisation retourne HTTP 202 et rejoint une file FIFO equitable entre sessions, avec deux validations simultanees par defaut. Elle verifie taille, SHA-256 complet, extension, signature/type reel, duree par `ffprobe`, decodage minimal et scanner optionnel. Un audio de plus de 5h est refuse; un warning est emis a partir de 4h30.

Les MP3 et OGG valides restent independants. Une piste invalide ne supprime pas les autres. La personne vaut par defaut le nom de fichier normalise sans extension, puis reste modifiable avant promotion vers le job.

Le client envoie au maximum trois fichiers a la fois, valeur issue de la configuration publique, et les chunks d'un meme fichier restent sequentiels.

## Securite

- Limiter taille de requete, taille de chunk, debit, duree d'inactivite, nombre de fichiers et sessions globales avant toute allocation disque importante.
- Ne faire confiance ni a l'extension, ni au MIME navigateur, ni aux metadonnees audio; valider signature, decodage et duree dans un processus borne.
- Proteger contre le slow upload avec timeouts lecture/ecriture et liberation garantie de la reservation apres inactivite.
- Un chunk invalide, rejoue ou hors offset ne doit ni ecraser des octets confirmes ni avancer l'etat; toutes les commandes restent autorisees par secret et revision.
- Le scanner antivirus optionnel s'execute avec timeout, sans acces inutile au reseau et sans droit d'ecriture hors zone de quarantaine.
- Nettoyer les noms originaux avant affichage pour eviter caracteres de controle, spoofing d'extension et injection dans logs ou headers.

## Validation

- Reprise apres coupure, fermeture de page et redemarrage serveur.
- Conflit propre entre deux clients envoyant le meme offset.
- Refus d'un chunk corrompu sans avance d'offset.
- Refus d'un faux MP3, d'un audio trop long et d'un SHA final incorrect.
- Conservation des pistes valides lorsqu'une autre echoue.
- Annulation pendant upload et finalisation, sans fichier orphelin durable.
- Tests de charge sans inference avec trois fichiers paralleles par session.
- Tests de slow upload, chunk geant, replay, MIME mensonger, fichier polyglotte et epuisement du quota disque.

## Definition de fin

Une session peut transferer plusieurs pistes, reprendre, valider, corriger les associations et devenir atomiquement eligible a la creation d'un job.
