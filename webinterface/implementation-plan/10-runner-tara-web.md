# Etape 10 - Runner Tara pour le web

## Objectif

Adapter le pipeline synchrone actuel de Tara au contrat web sans casser la CLI, avec evenements structures, annulation cooperative, limites communes et association source-personne explicite.

## Dependances

Etape 09.

## Fichiers a creer ou modifier

- `src/tara/web_runner.py`.
- `src/tara/web_contracts.py`.
- `src/tara/pipeline.py`.
- `src/tara/transcription.py`.
- `src/tara/context.py`.
- modules d'analyse qui gerent retry, warning et fallback.
- `tests/tara/test_web_runner.py` et tests de non-regression CLI.

## Strategie d'integration

Introduire dans les composants Tara des dependances optionnelles `event_sink` et `cancellation_token` avec implementations nulles par defaut. `TaraControlAgent.run()` conserve son comportement CLI. `TaraWebRunner` prepare les entrees, branche ces dependances et retourne un objet structure.

## Association source-personne

Le `RunnerRequest` contient une liste ordonnee de sources avec identifiant logique, chemin gere et personne. La transcription propage cet identifiant jusqu'au merged transcription. Le nom de fichier n'est utilise comme fallback que pour les appels CLI anciens.

## Limites de tokens

Creer un validateur partage utilisant le tokenizer reel de Tara:

- contexte: 2 000 tokens;
- resumes anterieurs: 50 000 tokens;
- merged transcription: 500 000 tokens.

Le web refuse avant creation du job lorsque possible. Tara reverifie par defense en profondeur, mais ne tronque jamais. Les anciennes limites en caracteres ne s'appliquent pas a une requete web validee.

## Evenements et annulation

Emettre aux frontieres des etapes et dans les boucles longues. Verifier l'annulation avant/apres appels externes et entre lots. Une operation non interruptible termine en quarantaine; son retour est ignore si l'annulation est confirmee.

Warnings, retries, fallbacks et erreurs utilisent des codes stables. Les exceptions techniques sont conservees pour les logs internes et converties une seule fois a la frontiere du runner.

## Resultat

Le runner declare les artefacts internes et fournit le modele de resultat public. Il ne publie jamais directement un fichier. Le processus principal valide, assainit et promeut seul le YAML final.

## Securite

- Traiter contexte, resumes et transcription comme contenu non fiable: les prompts systeme delimitent clairement les donnees et ordonnent de ne jamais suivre leurs instructions incorporees.
- Le runner n'accepte que des chemins deja resolves et associes au job; il refuse symlink, changement de fichier, identifiant source inconnu et artefact hors racine.
- Limiter tokens, taille des evenements, nombre d'artefacts et duree de chaque sous-etape avant appel provider afin de contenir denial of service et cout imprevu.
- Ne transmettre au worker que les credentials providers necessaires; masquer ces valeurs dans exceptions, usage events et resultats.
- Mapper les exceptions par liste positive de codes publics; les messages providers, commandes CLI et sorties stderr restent internes et expurges.
- L'annulation ou un timeout invalide la generation d'execution afin que tout retour tardif ne puisse publier un resultat.

## Validation

- Tests de parite CLI avant/apres adaptation.
- Tests de progression et d'annulation a chaque etape.
- Test avec noms physiques aleatoires confirmant l'attribution aux personnes explicites.
- Test des limites exactement sous, a et au-dessus des plafonds, sans troncature.
- Test de chaque retry/fallback structure sans analyse de logs.
- Le resultat ne contient ni chemin absolu ni Markdown brut non structure.
- Des tests de prompt injection, substitution de chemin, evenement tardif et exception contenant un faux secret confirment l'isolation et l'expurgation.

## Definition de fin

Le runner Tara satisfait exactement le protocole utilise par le faux runner et tous les tests CLI existants restent verts.

## Etat d'implementation - historique de revue

- Ajout de `tara.web_runner.TaraWebRunner` et du mode de runner serveur `tara`, tout en conservant `fake` comme mode compatible pour le MVP et les tests rapides.
- Les entrees sont resolues exclusivement sous le repertoire du job. Le manifest audio est relu avec une forme fermee, chaque fichier est verifie par taille et SHA-256, les liens symboliques, chemins manquants et substitutions sont refuses. Les pistes de travail sont des liens physiques internes, sans copie silencieuse des audios.
- Les personnes declarees dans le manifest sont propagees au merge par `process_transcriptions_with_authors`; la derivee depuis le nom reste le comportement CLI par defaut.
- Les plafonds de 2 000, 50 000 et 500 000 tokens sont controles avec `tiktoken` avant lancement et les contextes web ne sont jamais tronques. L'image Docker precharge la table BPE et la rend disponible en lecture seule au runtime, sans telechargement pendant un job.
- `TaraControlAgent` accepte de facon optionnelle un sink et un jeton d'annulation; sans ces dependances, le chemin CLI reste inchangé. Le runner emet les transitions d'etapes, confirme une annulation aux frontieres cooperatives, masque les exceptions techniques et ne declare que `work/final.yaml`; la promotion finale reste assuree par le processus SQLite proprietaire.
- Revue cycle 1: les liens symboliques ou reparse points sont refuses avant toute resolution de chemin, y compris lorsqu'ils visent une cible interne. Le cout runner reste indisponible tant qu'aucune devise explicite n'est portee par le contrat. Le manifeste inclut maintenant `source_id` et le merge l'utilise comme `source_file`.

Historique: apres le cycle 1, la tache restait non signee dans l'attente de la preparation durable des textes, des matrices de limites et des frontieres d'annulation. Ces points sont maintenant clos ci-dessous.

Validation executee:

- Ruff cible: `All checks passed`.
- Tests cibles runner, contrats CLI/web, transcription et orchestration: `27 passed, 2 skipped` lors de la revue cycle 1.
- Suite Python complete apres revue cycle 1: `695 passed, 10 skipped`.
- Construction `docker compose build tara-web-init` reussie, incluant le prechargement BPE et les controles frontend existants.

## Cloture de la tache

- Les textes de contexte et resumes anterieurs sont des artefacts geres, controles par taille et SHA-256, puis materialises par liens physiques idempotents dans le repertoire de travail.
- Les lectures sont bornees en octets avant decodage et tokenisation. Le decodage UTF-8 est strict et les fins de ligne sont normalisees comme une lecture texte Python, y compris sous Windows.
- Les evenements couvrent debut, progression et fin des etapes, retry LLM, fallback degrade, annulation et resultat. Les revisions sont strictement sequentielles, y compris avec des callbacks concurrents.
- L'annulation est verifiee avant et apres les appels externes et les lots. Une annulation LLM ne peut pas devenir un fallback de scenes et un resultat tardif ne peut pas publier le YAML final.
- Les callbacks web restent optionnels. Ils ne sont fournis aux composants de transcription et LLM qu'en mode web afin de conserver la CLI et ses signatures historiques.
- Le manifeste propage `source_id` jusqu'au merge et conserve la personne explicite sans exposer les noms physiques geres.
- Le cout du runner reste indisponible tant que la telemetrie par tentative et sa devise ne sont pas fournies; ce point appartient a l'etape 11.

Statut: signee le 2026-07-18 apres dix cycles de revue Terra Medium et validation independante Codex.

Validation finale:

- Ruff cible et format: `All checks passed`, 6 fichiers conformes.
- Tests cibles runner, callbacks, contrats CLI/web, transcription et orchestration: `156 passed, 3 skipped`.
- Tests de non-regression CLI apres correction de la frontiere LLM: `13 passed, 1 skipped`.
- Suite Python complete: `733 passed, 10 skipped`.
