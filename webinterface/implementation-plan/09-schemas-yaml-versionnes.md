# Etape 09 - Schemas YAML Tara versionnes

## Objectif

Donner a Tara des formats YAML canoniques, stricts et versionnes pour la merged transcription et le resultat public. Le Markdown et les traces restent internes ou CLI.

## Dependances

Etape 00. Cette tache peut avancer en parallele du MVP.

## Fichiers a creer ou modifier

- `src/tara/schemas/common.py`.
- `src/tara/schemas/merged_transcription.py`.
- `src/tara/schemas/public_result.py`.
- `src/tara/schemas/migrations/` et registre d'adaptateurs.
- `src/tara/yaml_utils.py`.
- `src/tara/analysis/models.py` et producteurs de sortie.
- `tests/tara/schemas/` avec fixtures valides, anciennes et invalides.
- `docs/schemas/merged-transcription.md`, `public-result.md`.

## Format et version

Definir une enveloppe incluant au minimum `schema_name`, `schema_version`, les metadonnees portables et le contenu type. La version courante initiale est `26.0.1`. Les modeles courants utilisent `extra="forbid"`.

Le chargeur suit cet algorithme:

1. parser YAML de maniere sure avec limites de taille/profondeur;
2. identifier schema et version;
3. choisir l'adaptateur exact;
4. migrer en memoire vers le modele canonique courant;
5. valider strictement;
6. retourner le modele courant, jamais un dictionnaire partiellement valide.

Les anciennes formes non versionnees ou JSON ne sont acceptees que par des adaptateurs explicites et testes. Une methode de parsing differente doit correspondre a une version ou un adaptateur documente.

## Resultat public

Le schema stabilise les blocs `paragraph`, `list`, `orderedList`, `keyValue`, `table`, `callout` et les sections metier `overview`, `chronology`, `characters`, `quests`, `combat`, `locations`, `items`, `factions`, `uncertainties`, `generic`.

Chaque section publique possede un `section_id` stable, unique dans le document et compatible avec un fragment d'URL, ainsi qu'un `section_type`, un titre lisible et ses blocs ordonnes. L'identifiant est genere/valide par Tara et ne derive jamais d'un chemin ou d'un contenu non borne. Le frontend suit l'ordre du tableau YAML et ne code pas les trois sections d'exemple de `concept_design_2.html`.

Une fonction de publication transforme le resultat interne en resultat public:

- normaliser les anciennes sections divergentes;
- convertir le Markdown interne en structures typees;
- supprimer chemins absolus, prompts, traces et identifiants internes;
- valider de nouveau le document public avant ecriture.

## Securite

- Utiliser exclusivement un chargeur YAML safe et des modeles stricts; interdire tags objets, constructeurs arbitraires, cles dupliquees et types implicites ambigus.
- Borner octets, profondeur, nombre de noeuds, longueur des scalaires, nombre d'alias et temps de parsing avant toute migration.
- Appliquer les limites a chaque etape d'adaptation afin qu'une ancienne version ne contourne pas les protections du modele courant.
- Considerer le texte transcrit et les resumes comme donnees non fiables pouvant contenir une prompt injection; ils restent separes des instructions systeme dans les appels LLM.
- Assainir la publication par liste positive de champs et types, jamais par suppression opportuniste d'une liste noire.
- Ne pas inclure de fonction generique permettant de charger un chemin ou schema choisi depuis le YAML lui-meme.

## Validation

- Golden tests de lecture/ecriture sans perte semantique.
- Tests d'unicite, stabilite et caracteres autorises de `section_id`, y compris apres adaptation d'une ancienne version.
- Tests de chaque adaptateur supporte vers `26.0.1`.
- Refus des champs inconnus, alias YAML dangereux, structures trop profondes et versions inconnues.
- Scan automatique des resultats publics contre chemins Windows/Linux, Markdown brut interdit et cles internes.
- Le CLI peut encore produire du Markdown; le web n'en depend pas.
- Un corpus hostile couvre alias exponentiels, profondeur excessive, cles dupliquees, tags Python, scalaires geants et tentative de fuite de champ interne.

## Definition de fin

Tara produit et consomme les deux schemas versionnes avec documentation, fixtures, adaptateurs et validation stricte.

## Etat d'implementation

Livre: `src/tara/schemas/` (modeles stricts, registre et migrations),
`src/tara/yaml_utils.py` (lecture YAML/JSON bornee), les integrations Tara et
web, les fixtures `tests/tara/schemas/fixtures/`, les golden/corpus hostile et
la documentation `docs/schemas/`.

Garanties: enveloppes `26.0.1`, `extra="forbid"`, adaptateurs exacts pour le
merged legacy et `final.yaml` v1, profils de limites, refus des alias/tags/
doublons/scalaires ambigus, publication par allowlist et controle des chemins,
champs internes et Markdown brut. Les resultats web exposent des sections
ordonnees avec `section_type` et blocs types.

Validation complete obtenue localement: `690 passed, 9 skipped` pour pytest et
`41 passed` pour le frontend. Le scenario Playwright reel sous Docker Compose
(deux audios, contexte, resumes, suivi, reouverture par lien et resultat) passe
egalement. Les artefacts produits dans le conteneur sont relus par le registre
strict comme `tara.public_result` version `26.0.1`.

Les commandes ciblees Task 09 restent
`uv run pytest tests/tara/schemas tests/tara/test_yaml_utils.py -q`,
`uv run pytest tests/web/api tests/web/storage -q`, `uv run ruff check` et
`uv run ruff format --check` sur les fichiers touches. Le lint cible est vert;
le lint global n'est pas une porte de validation car le depot conserve environ
500 violations historiques hors perimetre de cette tache.

Audit des dependances: apres verrouillage de `setuptools==83.0.0`,
`pip-audit` sur `uv export --frozen --all-groups --all-extras` ne trouve
aucune vulnerabilite connue. La synchronisation `uv sync --all-extras` et la
verification `uv lock --check` sont vertes.

Limite residuelle: le delai de parsing est cooperatif dans le processus courant.
L'isolation dans un worker avec arret dur est prevue par l'etape 13.

Statut au 2026-07-18: tache 09 terminee et signee comme base de regression pour
l'integration du runner Tara de l'etape 10.
