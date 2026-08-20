# Etape 12 - Integration audio reelle

## Objectif

Remplacer le faux runner par Tara reel pour le parcours audio direct, sans modifier les routes ni les composants frontend deja valides.

## Dependances

Etapes 08, 10 et 11.

## Fichiers a creer ou modifier

- `src/tara_web/runners/tara.py`.
- `src/tara_web/runners/factory.py`.
- `src/tara_web/orchestration/worker_entrypoint.py`.
- `src/tara_web/services/result_publication.py`.
- `tests/web/integration/test_real_audio_job.py`.
- fixtures audio multi-pistes et resultats de reference.

## Adaptateur web

L'adaptateur construit un `RunnerRequest` uniquement depuis un snapshot SQLite et des chemins resolves dans la racine du job. Il transmet langue, associations source-personne, contexte, resumes, configuration Tara figee et identifiants de correlation. Il ne transmet ni secret proprietaire ni objet repository.

Le worker initialise les clients providers apres demarrage du processus, pas avant le fork/spawn. Les valeurs sensibles proviennent de l'environnement du worker et ne reviennent jamais par IPC.

## Algorithme de traitement

1. Le scheduler revendique le job audio et fige sa configuration.
2. Le processus principal verifie une derniere fois la presence des artefacts d'entree.
3. Le worker execute `TaraWebRunner` et transmet ses evenements.
4. Le processus principal persiste les revisions, usages et declarations d'artefacts.
5. A la fin, il verifie le resultat structure et les artefacts declares.
6. Il publie le YAML public par ecriture atomique et SHA-256.
7. Il passe le job a `completed` uniquement apres promotion reussie.

Toute fin sans resultat public valide est un echec, meme si le pipeline interne a termine. Les artefacts internes restent dans `work/` et ne sont jamais servis.

## Progression

Mapper les etapes Tara vers:

- `Validation des entrees`;
- `Transcription`;
- `Preparation de la session`;
- `Analyse narrative`;
- `Synthese`;
- `Verification`;
- `Resultat pret`.

La transcription utilise la duree totale et les retours d'inference. Les autres etapes emettent d'abord une progression operationnelle reelle lorsqu'elle existe; l'estimation historique sera ajoutee a l'etape 15.

## Strategie de bascule

Conserver `runner_mode=fake|tara` dans la configuration de developpement. En production, refuser `fake` sauf option explicite. Executer les memes tests de contrat contre les deux implementations.

## Securite

- Revalider identite, etat `ready`, empreinte et appartenance de chaque entree juste avant la soumission au worker pour eviter substitution entre validation et usage.
- Executer Tara dans un environnement borne sans acces aux autres jobs; un identifiant logique ne doit jamais devenir directement un chemin.
- Separer les reseaux providers autorises des acces generaux si le mode de deploiement le permet et refuser toute destination dynamique issue des donnees.
- Appliquer les timeouts, limites de taille et bornes de concurrence avant le premier appel payant; une erreur de securite ou validation ne doit declencher aucune inference. La reservation monetaire transactionnelle et le plafond global sont livres par l'etape 15, qui depend de cette integration et de sa telemetrie. Tant que l'etape 15 n'est pas signee, l'increment audio ne constitue pas un deploiement public payant complet.
- Quarantainer les artefacts du worker jusqu'a validation stricte par le processus principal; seul ce dernier peut rendre le resultat public.
- Effacer les variables et fichiers temporaires sensibles a la fin du worker dans la mesure permise par la plateforme.

## Validation

- Job reel MP3, OGG et multi-pistes avec attribution correcte des personnes.
- Le job reel passe dans l'image Docker avec FFmpeg/ffprobe, certificats providers, process pool et permissions non-root.
- Progression, retry, warning, erreur et annulation visibles sans fuite provider.
- Parite des snapshots et actions entre faux et vrai runner.
- Resultat public valide, empreinte correcte et aucune reference serveur.
- Test de redemarrage apres progression reelle et relance via un nouveau job.
- Non-regression complete des tests Tara CLI.
- Un test remplace ou modifie une entree apres validation, simule un artefact worker hostile et confirme qu'aucun resultat n'est publie.
- Le conteneur n'a acces qu'aux volumes du job et ne requiert ni capability Linux ajoutee, ni mode privilegie.

## Definition de fin

Le parcours audio direct reel passe de bout en bout et le faux runner n'est plus utilise dans la configuration de production.

## Cloture de la tache

- La configuration de reference utilise `runner_mode: tara`; le faux runner reste disponible uniquement pour le developpement et exige une autorisation explicite avec une URL non locale.
- Le serveur charge la configuration Tara au demarrage, applique alors les surcharges operateur, la valide puis en fige une representation JSON bornee. Chaque `RunnerRequest` persiste ce snapshot; sa reconstruction dans le child ignore toute modification ulterieure de l'environnement.
- La factory importe Tara et initialise les clients providers uniquement apres le demarrage du processus `spawn`. Le parent reste seul proprietaire de SQLite et seul autorise a promouvoir le YAML public apres validation structurelle, ecriture atomique et SHA-256.
- Le parcours audio reel accepte plusieurs MP3/OGG, revalide taille et empreinte avant execution, conserve les identifiants de source opaques et attribue chaque personne depuis le nom du fichier. Deux corrections Windows imposent le mode binaire lors de l'upload et de la relecture du staging afin d'interdire toute conversion silencieuse des octets ou fins de ligne.
- L'image installe FFmpeg/ffprobe, le support Modal et le cache tiktoken. Le service non-root ne publie aucun port direct, ne monte pas `docker.sock`, separe le reseau provider du proxy et monte les configurations en lecture seule. `host.docker.internal` est mappe sur `host-gateway` pour Docker Engine compatible.
- Le smoke `scripts/smoke_real_audio_compose.py` construit l'image puis utilise un projet, un volume, un port et un sous-reseau sans chevauchement, tous jetables. Il soumet de vrais MP3 et OGG a un endpoint SSE local borne, verifie le resultat et l'attribution, recherche les fuites internes puis execute toujours `down -v --remove-orphans`.
- L'isolation inter-jobs est logique dans le worker et physique vis-a-vis de l'hote par le conteneur non-root. Une isolation par conteneur pour chaque job n'est pas introduite: elle exigerait un orchestrateur externe ou l'exposition interdite du socket Docker. Ce durcissement reste un risque residuel a reevaluer aux etapes 17 et 18; aucun chemin utilisateur concret n'a ete identifie avec les IDs opaques, chemins geres, controles no-follow, empreintes et quarantaine actuels.
- La reservation de budget globale n'est pas dupliquee ici: l'etape 15 en est explicitement proprietaire. Avant cette etape, les appels sont bornes par taille, duree, concurrence et timeout, mais l'increment n'est pas considere comme un service public payant complet.

Statut: signee le 2026-07-18 apres six cycles de revue Terra Medium, corrections Codex et validation host/conteneur.

Validation finale:

- Ruff sur les modules, tests et smoke concernes: `All checks passed`.
- Tests cibles runner, configuration, orchestration, upload, deploiement et integration: `144 passed, 4 skipped`, puis `127 passed, 3 skipped` apres revue.
- Suite Python complete: `781 passed, 11 skipped`.
- Image Docker reconstruite avec l'extra `deploy`; services non-root sains et endpoints `/api/v1/live` et `/api/v1/ready` disponibles via le proxy.
- Smoke Docker audio reel: MP3 + OGG, deux appels SSE, progression, resultat public disponible, personnes `Alice` et `MaitreDuJeu`, deux sources distinctes et aucune valeur interne publique.
