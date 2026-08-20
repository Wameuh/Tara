# Etape 03 - Stockage gere et artefacts

## Objectif

Garantir l'isolation des fichiers, l'atomicite des ecritures, l'integrite du resultat et un nettoyage recuperable apres crash.

## Dependances

Etape 02.

## Fichiers a creer ou modifier

- `src/tara_web/storage/layout.py`: resolution et controle des chemins.
- `src/tara_web/storage/artifacts.py`: cycle de vie des artefacts.
- `src/tara_web/storage/atomic.py`: ecriture temporaire, fsync, renommage.
- `src/tara_web/storage/reconciliation.py`: remise en coherence au demarrage.
- `src/tara_web/storage/cleanup.py`: retention et orphelins.
- `tests/web/storage/`: traversal, crash simule, integrite et suppression.

## Arborescence

```text
{storage_root}/jobs/{job_id}/inputs/
{storage_root}/jobs/{job_id}/work/
{storage_root}/jobs/{job_id}/result/
{storage_root}/uploads/{session_id}/
```

Le backend genere chaque nom physique et conserve le nom original uniquement comme metadonnee temporaire. Toute resolution utilise `Path.resolve()` puis verifie l'appartenance a la racine attendue. Les symlinks, chemins absolus fournis et sequences de traversal sont refuses.

Sous Docker, `storage_root` pointe vers un volume dedie monte a un chemin fixe du conteneur. Le code ne recoit jamais le chemin hote correspondant. `inputs`, `work` et `result` ne sont servis ni comme volume du reverse proxy ni comme repertoire statique.

## Algorithme d'ecriture d'artefact

1. Creer une ligne `job_artifacts` en etat `pending`.
2. Ecrire par blocs dans un fichier temporaire du meme volume.
3. Pour le YAML final, calculer SHA-256 pendant l'ecriture.
4. Vider et synchroniser le fichier puis son dossier si la plateforme le permet.
5. Renommer atomiquement vers le nom final.
6. Valider le schema public et l'absence de donnees internes.
7. Passer la ligne a `ready` avec taille, empreinte et expiration.

Un artefact obligatoire en echec fait echouer le job avec `artifact_write_failed`. `cache`, `prompt` et `technical_error` sont facultatifs et emettent un avertissement structure.

## Suppression et reconciliation

- Transition `ready|error -> deleting -> deleted`.
- Trois essais courts: immediat, puis 100 ms et 500 ms.
- Un fichier deja absent est considere supprime et peut produire un avertissement.
- Au demarrage, reprendre `pending` et `deleting` selon l'etat physique.
- Chaque heure, supprimer les artefacts expires et les orphelins geres ages de plus d'une heure.
- Ne jamais parcourir ni supprimer en dehors des racines configurees.

## Securite

- Ouvrir les fichiers sans suivre les liens lorsque la plateforme le permet et reverifier le chemin resolu juste avant ecriture, promotion, lecture et suppression.
- Creer dossiers et fichiers avec permissions privees; ne jamais rendre directement la racine de stockage statique dans FastAPI ou le reverse proxy.
- Borner octets, nombre d'artefacts et espace reserve par session/job; verifier le seuil disque avant et pendant les grosses ecritures.
- Servir un resultat uniquement via un descripteur ouvert apres autorisation et controle SHA-256, avec nom de telechargement assaini si un export est ajoute plus tard.
- Separer physiquement ou logiquement `inputs`, `work` et `result`; un worker ne peut pas choisir le chemin public final.
- Journaliser les anomalies de traversal, symlink, empreinte et suppression sous codes stables sans inclure le chemin utilisateur brut.

## Validation

- Des tests d'injection couvrent `../`, chemins absolus, doubles extensions, symlinks et noms Unicode ambigus.
- Un crash est simule apres chaque etape de promotion et de suppression; la reconciliation converge vers un etat coherent.
- Une empreinte finale incorrecte rend le resultat indisponible avec `result_integrity_failed` sans recalcul silencieux de la reference.
- Les noms originaux deviennent `NULL` apres suppression physique.
- Une suppression repetee est idempotente.
- Des courses de remplacement par symlink sont simulees entre validation et ouverture; aucune operation ne sort de la racine.
- Une recreation du conteneur conserve les artefacts persistants, tandis que la racine read-only et les chemins non montes restent non ecrivables.

## Definition de fin

Le service peut creer, promouvoir, verifier, expirer et reconciler tout type d'artefact sans exposer de chemin non gere.
