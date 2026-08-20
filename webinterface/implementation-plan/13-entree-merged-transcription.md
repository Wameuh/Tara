# Etape 13 - Entree merged transcription YAML

## Objectif

Permettre de lancer l'analyse sans audio a partir d'un `merged_transcription.yaml` ou `.yml` versionne et strictement valide.

## Dependances

Etapes 09 et 12.

## Fichiers a creer ou modifier

- `src/tara_web/services/merged_transcription_validation.py`.
- extension de `api/routes/uploads.py` et des snapshots d'entree.
- extension de `runners/tara.py`.
- composants frontend `features/upload/MergedTranscriptionInput.tsx`.
- fixtures de chaque version supportee et tests d'integration.

## Flux

1. Creer une session du type `merged_transcription`.
2. Transferer le YAML par le meme protocole reprenable si sa taille le justifie.
3. Parser avec le chargeur Tara versionne dans une operation de validation bornee.
4. Migrer en memoire vers le modele canonique et compter les tokens avec Tara.
5. Refuser au-dessus de 500 000 tokens, sans troncature.
6. Renvoyer des erreurs ciblees par chemin de champ ou position disponible.
7. Promouvoir l'entree valide et lancer le job quand les autres champs sont prets.

Le backend ne repare pas automatiquement le YAML. Les corrections de locuteurs sont fournies via le contexte texte en V1, pas par un mapping structure additionnel.

## Securite du parseur

- Parseur safe, pas de construction d'objet arbitraire.
- Limites configurables sur octets, profondeur, nombre de noeuds, alias et temps CPU.
- Traitement dans un worker de validation si un document peut monopoliser le processus HTTP.
- Erreurs techniques internes mappees vers un code public stable.
- Refuser cles dupliquees, tags arbitraires, ancres/alias excessifs, document multi-parties inattendu et encodage invalide.
- Executer parsing et migration sous limites memoire/CPU; tuer le worker de validation au timeout sans affecter le serveur HTTP.
- Traiter chaque texte du document comme donnees, jamais comme instruction, nom de fichier, URL ou chemin a ouvrir.
- N'exposer dans les erreurs que chemins de champs et positions bornes; ne pas recopier de larges extraits potentiellement sensibles.
- Verifier que le document valide appartient toujours a la session et conserve la meme empreinte avant promotion.

## Frontend

Le formulaire rend les modes audio et merged transcription mutuellement exclusifs. Il conserve contexte et resumes lors d'une erreur de YAML ou d'un changement de fichier. La version detectee et la mesure backend peuvent apparaitre dans les details de validation, sans afficher le YAML brut apres lancement.

## Validation

- YAML `26.0.1`, anciennes versions supportees, extension `.yaml` et `.yml`.
- Refus version inconnue, champ inconnu, document mal forme, alias excessifs et limite tokens.
- Messages localisables et sans stack trace.
- Parcours complet vers le meme schema de resultat public que l'audio.
- Aucun code de transcription audio n'est appele pour ce mode.
- Le corpus de securite YAML ne bloque pas le processus HTTP et ne produit ni allocation durable ni fuite de contenu dans les erreurs.

## Definition de fin

Une merged transcription valide peut lancer le pipeline reel et produit un resultat identique en contrat au parcours audio.

## Cloture de la tache

- Les sessions `merged_transcription` sont exclusives des sessions audio et utilisent
  le protocole reprenable existant pour un unique fichier `.yaml` ou `.yml`.
- La migration 13 persiste le type d'entree, le schema detecte, la version, le nombre
  de tokens canoniques et un chemin d'erreur public borne. La promotion reverifie
  l'appartenance, l'activite, la taille et le SHA-256 avant de deplacer l'entree vers
  un artefact YAML immuable.
- Le parseur s'execute dans un processus `spawn` tuable, sans alias par defaut, avec
  limites configurables d'octets, profondeur, noeuds, scalaires, memoire, CPU et temps
  mural. Le comptage porte uniquement sur le texte canonique de la transcription.
- Le runner recoit directement le YAML gere avec le contexte et les resumes; le test
  HTTP reel prouve que le chemin de transcription audio n'est jamais appele et que le
  resultat public conserve le meme contrat.
- Le formulaire React rend les modes audio/YAML mutuellement exclusifs, conserve les
  textes lors du changement de fichier et affiche version, tokens et chemin d'erreur
  depuis les types OpenAPI generes, sans recopier le contenu YAML.
- Les erreurs de stockage et les liens symboliques sont normalises a la frontiere du
  workspace afin qu'aucun detail interne ne remonte au scheduler ou a l'utilisateur.

Statut: signee le 2026-08-19 apres reprise du journal interrompu, revue du contrat et
validation Windows/Linux.

Validation finale:

- Frontend: `45 passed`, lint, build, OpenAPI, i18n et budget de bundle valides.
- Suite web Windows: `258 passed, 9 skipped`; l'unique test IPC intermittent a passe
  en relance isolee.
- Linux ARM64: Ruff propre et `20 passed` sur validation YAML, promotion immuable et
  securite du workspace.
- Parcours HTTP reel valide jusqu'au resultat public, sans dossier ni appel de
  transcription audio.
