# Etape 08 - Validation du MVP vertical

## Objectif

Prouver que le premier increment est un produit vertical coherent et non une juxtaposition de modules.

## Dependances

Etapes 07, 07A et 07B.

## Fichiers a creer ou modifier

- `tests/web/e2e/` ou `webinterface/frontend/e2e/` pour Playwright.
- `tests/web/load/` pour les scenarios synthetiques.
- `webinterface/frontend/e2e/concepts/job-progress.spec.ts` et `result-view.spec.ts`.
- `webinterface/frontend/e2e/concepts/brand-logo.spec.ts`.
- snapshots visuels de reference produits depuis l'implementation React, relies aux deux concepts HTML.
- `docs/webinterface/mvp-validation.md` pour les resultats et limites connues.
- fixtures deterministes du faux runner.

## Scenarios E2E obligatoires

1. Creation avec plusieurs audios, personnes modifiees, contexte et resumes.
2. Upload interrompu puis repris depuis l'offset serveur.
3. Attente FIFO, progression double, rafraichissement et resultat dynamique.
4. Reouverture par lien dans un nouveau contexte navigateur.
5. Lien invalide et secret regenere.
6. Erreur simulee puis `Modifier et relancer` avec nouveau compteur de retention.
7. Timeout, unique relance identique, puis relance modifiee.
8. Annulation en attente et pendant l'execution.
9. Expiration d'un intermediaire et du resultat final avec etats explicites.
10. Perte SSE avec resynchronisation et polling.
11. Suivi conforme a `concept_design_1.html`: deux progressions, chronologie, informations, copie du lien, annulation et passage dynamique au resultat.
12. Resultat conforme a `concept_design_2.html`: navigation responsive, recherche, compteur, surlignage, accordions, copie et cout/expiration.

## Validation des concepts UI

La validation ne cherche pas une copie pixel-perfect des prototypes, car le theme reste un chantier separe. Elle verifie la composition, la hierarchie, la densite, les comportements et l'adaptation aux donnees reelles.

Viewports minimaux:

- desktop large `1440 x 900`;
- desktop compact/tablette `980 x 900` autour du changement de navigation;
- mobile `390 x 844`;
- mobile etroit `320 x 720` pour les textes longs et controles.

Pour le suivi:

- injecter chaque statut de job et chaque etat d'etape;
- verifier que les pourcentages courant et total sont distincts, accessibles et ne provoquent aucun changement de largeur;
- tester estimation disponible, indisponible et temporairement suspendue;
- verifier les textes longs de sous-etape, le numero de tentative et les actions conditionnelles;
- confirmer qu'un passage `completed` remplace le suivi par le resultat sans perdre le secret en memoire.

Pour le resultat:

- generer 0, 1, 3 et de nombreuses sections dans des ordres differents;
- couvrir tous les `section_type` et blocs publics V1;
- verifier navigation sticky desktop, selecteur mobile et synchronisation avec la section visible;
- tester recherche sans resultat, un resultat, nombreux resultats, caracteres accentues et texte tres long;
- verifier ouverture automatique des sections correspondantes, `Tout ouvrir`, `Tout fermer` et conservation d'un focus coherent;
- verifier le texte exact copie pour une section et l'absence de YAML/metadata technique;
- verifier la copie d'un lien cible, son avertissement, le fragment secret+section et la restauration du focus apres reouverture;
- tester cout complet, partiel et indisponible ainsi que l'expiration proche/expiree.

Capturer des screenshots Playwright apres stabilisation des polices et animations. Comparer les regressions de structure avec une tolerance documentee; toute difference intentionnelle est revue et la baseline est mise a jour dans le meme changement.

## Tests de charge sans inference

Un scenario reproductible doit couvrir plusieurs uploads, 5 jobs actifs, 25 en attente, SSE et polling concurrents, annulations et nettoyage. Mesurer latence API, contention SQLite, profondeur IPC, memoire et nombre de descripteurs. Le test doit confirmer que le processus reste pret et que les limites ne sont jamais depassees.

## Tests de crash

Injecter un arret apres reservation, chunk, finalisation, promotion, revendication de job, progression, ecriture de resultat et suppression. Redemarrer, executer la reconciliation, puis verifier les invariants SQLite/fichiers.

## Securite

- Ajouter un parcours abusif par frontiere: API sans secret, upload hostile, evenement IPC falsifie, resultat corrompu, entree XSS et surcharge SSE/polling.
- Capturer requetes, reponses, logs et base de test, puis rechercher automatiquement secrets, fragments d'URL, contenus de contexte et chemins absolus.
- Executer une analyse de dependances Python/npm et un scan statique minimal; toute alerte critique ou elevee doit etre corrigee ou explicitement bloquer le jalon.
- Verifier que les limites restent effectives sous concurrence et qu'une erreur de securite n'entraine ni boucle de retry, ni consommation provider, ni fuite de capacite.
- Conserver les corpus malveillants et cas de regression dans des fixtures sans secret reel.

## Validation et criteres d'acceptation

- Tous les E2E passent sur Chromium.
- Le MVP est execute par Docker Compose avec le reverse proxy comme seul port publie, `tara-web` non-root et ses volumes dedies.
- Les tests backend et frontend sont deterministes et ne dependent d'aucun provider.
- Aucune fuite de secret, chemin, log ou contenu intermediaire dans les captures reseau.
- Les artefacts du faux runner sont marques comme artificiels en interne et exclus de `job_metrics`.
- Le rapport liste explicitement les fonctions differees: Tara reel, entree YAML et ZIP.
- Le rapport contient une section securite avec controles testes, resultats, ecarts et proprietaire de chaque correction.
- Les screenshots des quatre viewports ne montrent aucun chevauchement, debordement horizontal, texte coupe ou controle inaccessible.
- Axe/ARIA ne signale aucun controle interactif imbrique; les deux progressions, accordions, recherche, toasts et navigation de sections sont annonces correctement.
- Les concepts HTML restent references dans le rapport, avec la liste des adaptations volontaires: double progression, sections dynamiques, controles de copie separes et icones de bibliotheque.
- Le logo noir/blanc, le fallback statique, l'animation unique du D20, reduced motion, dimensions et absence de layout shift sont valides selon 07B.

## Definition de fin

Le rapport de validation est signe comme base de regression. Il inclut le digest de l'image MVP et la configuration Compose rendue. L'integration Tara peut commencer sans modifier les contrats API ou frontend du MVP.
