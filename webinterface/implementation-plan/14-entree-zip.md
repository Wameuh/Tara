# Etape 14 - Archives ZIP audio

## Objectif

Ajouter l'upload d'un ZIP contenant des MP3/OGG, son extraction securisee et la validation utilisateur des associations piste-personne avant lancement automatique.

## Dependances

Etape 12.

## Fichiers a creer ou modifier

- `src/tara_web/services/zip_validation.py`.
- `src/tara_web/services/zip_extraction.py`.
- extension du scheduler de validations.
- composant frontend `features/upload/ZipInput.tsx` et liste des pistes extraites.
- `tests/web/zip/` avec corpus sain et malveillant.

## Algorithme d'extraction

1. Recevoir le ZIP complet par upload reprenable et verifier son SHA-256.
2. Ouvrir l'archive sans extraire et inspecter toutes les entrees.
3. Refuser archive chiffree, corrompue, entree absolue, traversal, lien ou type special.
4. Calculer de maniere bornee le nombre de fichiers, la taille decompressee annoncee et le rapport de compression.
5. Rechercher recursivement `.mp3` et `.ogg`; comptabiliser les exclusions.
6. Generer un nom physique pour chaque piste et extraire en flux dans la racine de session.
7. Verifier pendant l'ecriture que les limites reelles ne depassent pas les annonces.
8. Soumettre chaque piste au validateur audio normal.
9. Supprimer le ZIP original apres extraction et validation reussies.

Les seuils de nombre, taille decompressee et ratio sont configurables, pas des limites produit fixes.

## Equite et annulation

Extraction et validations rejoignent la file de finalisation, avec rotation entre sessions. Une grosse archive ne monopolise pas les deux places. L'annulation cesse les nouvelles extractions, laisse finir l'ecriture atomique en cours si necessaire, puis nettoie les sorties partielles.

## Securite

- Ne jamais appeler un outil d'extraction shell avec un nom fourni; utiliser une bibliotheque qui permet d'inspecter et extraire chaque entree sous controle.
- Refuser chemins absolus, `..`, prefixes de volume Windows, chemins UNC, liens, devices, entrees speciales et collisions apres normalisation/casse.
- Appliquer simultanement limites sur taille compressee, taille decompressee declaree et reelle, nombre d'entrees, profondeur, ratio et temps CPU.
- Ecrire chaque piste sous un nom genere avec creation exclusive; reverifier la racine apres ouverture pour resister aux courses et liens.
- Ne pas extraire les fichiers non audio, meme temporairement; enregistrer uniquement leur nombre/type general dans le recapitulatif.
- Isoler l'extraction dans un worker sans reseau et avec quotas disque/memoire lorsque la plateforme le permet.

## Interaction utilisateur

Afficher `Transfert`, `Extraction`, `Validation des pistes`, `Preparation du lancement`, avec progressions courante et globale. Presenter les pistes valides et exclues, initialiser la personne depuis le nom et permettre la modification. La validation des associations est la derniere action utilisateur et declenche automatiquement le job.

## Validation

- ZIP plat et imbrique, noms identiques dans plusieurs dossiers, MP3/OGG melanges.
- ZIP sans audio, chiffre, corrompu, zip bomb, traversal, lien symbolique et taille mensongere.
- Annulation et crash a chaque etape sans extraction hors racine ni orphelin durable.
- Conservation des pistes valides lorsqu'une piste extraite est invalide.
- Le ZIP original disparait apres succes; les pistes suivent la retention intermediaire.
- E2E complet ZIP avant declaration de la beta.
- Un corpus ZIP malveillant multi-plateforme couvre traversal encode, collision Unicode/casse, symlink, device Windows et bombes a tailles mensongeres.

## Definition de fin

Le mode ZIP est securise, reprenable, equitable et produit exactement les memes entrees logiques que l'upload audio direct.

## Cloture de la tache

Cloturee le 2026-08-20.

- upload reprenable d'une archive unique et extraction en flux sous noms physiques generes;
- inspection bornee contre chiffrement, corruption, traversal multi-plateforme, liens, devices Windows, collisions Unicode/casse et bombes de compression;
- rotation par session dans le scheduler borne, annulation cooperative et nettoyage des sorties partielles/orphelines;
- validation audio normale de chaque piste, conservation des pistes invalides, suppression du ZIP source et recapitulatif des exclusions;
- phases `Transfert`, `Extraction`, `Validation des pistes` et `Preparation du lancement`, associations piste-personne modifiables puis lancement explicite;
- validations: Ruff propre, 45 tests backend ZIP/API cibles, 48 tests frontend, lint et build de production, plus E2E reel ZIP MP3/OGG jusqu'au resultat public.

L'isolation process sans reseau et les quotas memoire restent dependants de la plateforme de deploiement; aucune commande shell d'extraction n'est utilisee.
