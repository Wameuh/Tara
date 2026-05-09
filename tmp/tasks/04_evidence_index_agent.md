# 04 - EvidenceIndexAgent et retrieval local

## Objectif

Construire un index local depuis `merged_transcription.json` afin que les LLM ne relisent jamais toute la transcription.

## A recuperer de Tara

- `src/tara/processing/models.py` pour comprendre le format des segments.
- `merged_transcription.json` comme entree officielle.
- `telemetry` pour mesurer nombre de chunks, taille indexee, temps de traitement.

## A ne pas recuperer tel quel

- `TranscriptionSplitterAgent`: le decoupage par scene n'est plus central.
- `SceneAnalyzerAgent`: ne plus demander au LLM de lire toute la transcription.

## Outputs

- `evidence_chunks.jsonl`
- `evidence_index_metadata.json`
- in-memory index reloadable from `evidence_chunks.jsonl` and
  `evidence_index_metadata.json` as the current local equivalent to SQLite

## Taches

1. Charger `merged_transcription.json`.
2. Creer des chunks glissants:
   - duree cible: 60 a 120 secondes;
   - overlap: 15 a 30 secondes;
   - conserver ids de segments et timestamps.
3. Ajouter metadonnees:
   - personnages detectes;
   - mots cles: soin, mort, fuite, ennemi, ressource, lieu, objet, mecanisme;
   - position temporelle.
4. Implementer retrieval lexical:
   - simple score token overlap au depart;
   - BM25 si dependance acceptable.
5. Ajouter embeddings locaux si disponible et non couteux:
   - optionnel;
   - fallback lexical obligatoire.
6. Exposer API:

```python
retrieve(query: RetrievalQuery, limit: int) -> list[RetrievedEvidence]
```

7. Ajouter un coverage report:
   - chunks crees;
   - duree couverte;
   - zones sans texte;
   - zones peu indexees.

## Criteres de validation

- `Record19` est indexe sans appel LLM lors du smoke test manuel, sans copier
  ses artefacts prives dans le depot.
- Chaque chunk pointe vers les segments originaux.
- Retrieval lexical retrouve des extraits pour des requetes simples comme "Molnir mort", "lance", "sanctuaire", "potion".
- L'index couvre toute la duree de la transcription.

## Implementation Status

Status: Completed

Notes:

- `src/tara/analysis/evidence_index/retrieval.py` implements an in-memory
  `EvidenceIndex` over `MergedTranscription`.
- Chunking uses sliding time windows with configurable target duration and
  overlap, while preserving source segment IDs and timestamps on every
  `EvidenceChunk`.
- Metadata includes simple capitalized-entity detection, coarse keyword tags
  (`combat`, `healing`, `escape`, `resource`, `place`, `object`), and temporal
  position.
- Retrieval is local CPU-only lexical token-overlap scoring with accent
  normalization. No LLM call and no external embedding API is used.
- Export helpers write `evidence_chunks.jsonl` and
  `evidence_index_metadata.json`, and `EvidenceIndex.from_artifacts` can reload
  the in-memory index from those artifacts.
- `EvidenceCoverageReport` records chunk count, indexed segments, covered
  duration, uncovered ranges, and empty-text segment IDs.
- `Record19` validation remains a manual privacy-preserving smoke check because
  private session artifacts are not loaded by automated tests.
- Operational limits guard large inputs: maximum segments, maximum segment text
  length, maximum chunks, and maximum query tokens.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest "tests/tara/analysis"`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
