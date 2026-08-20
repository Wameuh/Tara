# Etape 15 - Estimation, cout et budget global

## Objectif

Fournir une progression et un temps restant ajustes par historique reel, afficher le cout approximatif au resultat et faire respecter le plafond global d'inference.

## Dependances

Etapes 11 et 12.

## Fichiers a creer ou modifier

- `src/tara_web/estimation/features.py`, `regression.py`, `service.py`.
- `src/tara_web/services/costs.py`, `budget.py`, `circuit_breaker.py`.
- repositories `job_metrics`, `job_failure_metrics`, `provider_circuits`.
- routes/metriques d'exploitation privees si necessaires.
- composants frontend de progression et cout resultat.
- tests statistiques, budget et circuits.

## Donnees d'historique

`job_metrics` contient une ligne seulement a la fin d'un job reel reussi. Colonnes minimales: session/job anonymise, type d'entree, duree audio totale, nombre de pistes, tokens merged transcription, duree de transcription et de chaque etape, tokens et couts par famille, date/version/configuration utile.

Les echecs, annulations et timeouts n'alimentent pas la regression. Ils incrementent `job_failure_metrics`, agrege par jour et statut, avec compteurs de nombre, temps, tokens et cout. Les erreurs avant job alimentent `pre_job_error_metrics` par date, code et type d'entree.

## Regression et progression

Utiliser des regressions lineaires simples et explicables par type d'entree/etape:

- transcription: duree audio comme variable principale;
- preparation, analyse, synthese, verification: duree audio et/ou tokens merged selon pertinence mesuree;
- entree merged YAML: tokens de transcription;
- attente: travail estime des jobs devant celui-ci et capacite active.

Algorithme:

1. filtrer les jobs reussis compatibles avec la version/famille;
2. exiger un nombre minimal configurable d'observations;
3. ajuster une regression lineaire avec intercept borne a une valeur non negative;
4. calculer prediction et indicateur de confiance simple;
5. pendant une etape, combiner progression mesuree et temps passe/temps predit sans faire reculer brutalement le pourcentage;
6. recalculer le total apres chaque etape avec les durees reelles acquises.

Avant historique suffisant, afficher la progression mesurable mais pas de fausse estimation temporelle. Les donnees du faux runner sont exclues.

## Projection dans le concept de suivi

Le composant `DualProgress` de l'etape 07 consomme deux valeurs distinctes:

- progression totale du job, presentee comme la barre principale de `concept_design_1.html`;
- progression de l'operation courante, presentee dans l'etape active avec son libelle.

Le panneau `Informations du traitement` affiche l'estimation uniquement lorsque `estimate_available=true`. Il affiche sinon un libelle localisable neutre, sans valeur factice. Une nouvelle estimation ne doit jamais faire reculer brutalement une progression deja vue; les corrections sont lissees et l'heure restante peut augmenter avec une explication courte lors d'un retry ou circuit ouvert.

## Cout utilisateur

Agreger toutes les tentatives connues, y compris echouees, convertir en euros avec la table de taux/prix snapshottee, puis afficher `Cout approximatif du processus` a cinq decimales uniquement sur le resultat. Si une partie est inconnue, afficher un total partiel explicitement marque. Le cout n'entre pas dans le YAML final.

Dans `concept_design_2.html`, cette valeur occupe la zone laterale du bandeau de resultat. Le contrat frontend recoit valeur formatee ou valeur numerique+devise, qualite `complete|partial|unavailable` et cle d'explication localisable. Le symbole d'information est un vrai bouton avec tooltip/dialog accessible, pas un caractere decoratif; il explique qu'il s'agit d'une approximation et ce que signifie un total partiel.

## Budget et circuit breaker

- Avant tout nouveau lancement payant, reserver conservativement un montant estime dans une transaction.
- A l'atteinte du plafond configurable, laisser finir les jobs actifs et refuser les nouveaux lancements payants.
- Reconnaitre le cout reel/estime final et liberer l'ecart de reservation.
- Maintenir un circuit par provider et famille d'operation, avec etat persiste, seuils et delai configurables.
- Un circuit ouvert retarde les nouvelles etapes dependantes mais n'arrete pas un appel deja engage et ne rend pas `ready` negatif.

## Securite

- Considerer le budget comme une limite transactionnelle de securite: reservation et consommation doivent etre atomiques et utiliser des entiers, sans flottants ni valeur negative.
- Refuser les metriques non finies, negatives, hors bornes ou provenant d'une version inconnue avant insertion ou apprentissage.
- Eviter qu'un utilisateur influence directement les variables du modele hors entrees validees; borner les predictions et ne jamais exposer les lignes historiques.
- Separer metriques publiques, telemetrie operateur et donnees de cout providers; aucune route publique ne retourne identifiant provider, modele interne ou historique global.
- Proteger la reouverture manuelle du budget et des circuits par une commande d'exploitation locale/privee, auditee et non disponible avec le secret d'un job.
- Detecter les doubles evenements d'usage par identifiant de tentative afin d'eviter double facturation ou contournement de reservation.

## Validation

- Jeux synthetiques avec coefficients connus et tests de regression reproductibles.
- Aucune estimation affichee sous le seuil d'echantillons.
- Deux pourcentages stables, bornes et termines exactement a 100%.
- Les etats d'estimation disponible/indisponible et de cout complet/partiel/indisponible alimentent correctement les composants derives des concepts.
- Tests concurrents prouvant que le budget ne peut pas etre depasse par de nouvelles reservations.
- Tous les couts sont en euros, cinq decimales, avec provenance et caractere approximatif.
- Circuits isoles par famille, persistants apres redemarrage et sans double retry.
- Tests de course, overflow, valeurs `NaN`/infinies, usage duplique et tentative d'appeler les commandes operateur avec un secret de job.

## Definition de fin

Les estimations reposent uniquement sur l'historique reel, les couts sont transparents sur leurs limites et le plafond protege effectivement le service public anonyme.

## Cloture de la tache

Cloturee le 2026-08-20.

- regressions lineaires explicables par type et etape, seuil minimal configurable, confiance bornee et absence d'estimation avant historique suffisant;
- attente des jobs en file integree a l'estimation et progression courante/totale monotone au sein d'une etape;
- metriques de succes ecrites uniquement par le runner Tara reel, echecs agreges separement et historique conserve hors purge operationnelle;
- cout complet, partiel ou indisponible sur le resultat uniquement, en micro-euros puis formate a cinq decimales, avec bouton d'explication accessible;
- reservation globale atomique avant chaque tentative payante, reconciliation idempotente du cout connu et conservation prudente de la reservation si le cout est partiel;
- circuits persistants par provider/famille, une seule sonde half-open et blocage des nouveaux jobs sans interrompre les appels actifs;
- commandes operateur locales et auditees pour fermer un circuit ou modifier le plafond; aucune route publique correspondante;
- validations: Ruff propre, 69 tests backend cibles, 48 tests frontend, lint et build de production, E2E ZIP reel confirmant aussi l'ecriture d'un echantillon historique.
