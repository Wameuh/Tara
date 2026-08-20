# Etape 07 - Frontend fonctionnel du MVP

## Objectif

Construire le parcours utilisateur complet du MVP en traduisant les deux concepts HTML en composants React. Leur composition et leurs interactions constituent la reference de la V1; le theme visuel pourra encore etre retravaille separement.

## Dependances

Etape 06. La sous-tache `07a-specifications-visuelles-concepts.md` est la reference mesurable pour les styles, grilles, breakpoints et etats visuels. La sous-tache `07b-logo-et-animation.md` definit l'identite Tara et le mouvement du D20.

## Fichiers a creer ou modifier

- `webinterface/frontend/src/api/client.ts`, `problemDetails.ts`, `sse.ts`.
- `src/features/upload/`, `job/`, `result/`, `relaunch/`.
- `src/pages/NewJobPage.tsx`, `UploadSessionPage.tsx`, `JobPage.tsx`.
- `src/components/AppHeader.tsx`, `ToastRegion.tsx` et primitives de layout partagees.
- `src/components/brand/TaraLogo.tsx`, `AnimatedTaraLogo.tsx`, `BrandLink.tsx` et `logo.css`.
- `src/features/job/components/DualProgress.tsx`, `StageTimeline.tsx`, `JobInfoPanel.tsx`, `JobActions.tsx`.
- `src/features/result/components/ResultHeader.tsx`, `ResultNavigation.tsx`, `ResultSearch.tsx`, `ResultSection.tsx`, `PublicBlockRenderer.tsx`.
- `src/features/job/hooks/useJobSnapshot.ts`, `useJobEvents.ts`.
- `src/features/result/hooks/useResultSearch.ts`, `useSectionNavigation.ts`, `useReadableClipboard.ts`.
- `src/styles/tokens.css`, `layout.css` et styles de composants sans recopier les styles inline des prototypes.
- `webinterface/implementation-plan/07a-specifications-visuelles-concepts.md` comme specification visuelle de reference.
- `webinterface/implementation-plan/07b-logo-et-animation.md` comme specification d'assets et de mouvement.
- `src/i18n/fr/*.json`, manifeste et validation des catalogues.
- `src/storage/draft.ts`: brouillon local non sensible.
- tests Vitest/Testing Library et `webinterface/frontend/e2e/`.

## Traduction des concepts HTML et parcours

Les references sont:

- `webinterface/concept_design_1.html` pour le suivi;
- `webinterface/concept_design_2.html` pour le resultat.

Elles servent de specification de composition, de hierarchie d'information, de responsive et d'interactions. Ne pas importer les fichiers HTML, copier leurs scripts dans React ou faire dependre la production de leur DOM. Les donnees affichees proviennent uniquement des snapshots/API et du schema public.

Le shell commun reprend un en-tete Tara compact avec le logo officiel noir, lien de retour logique, aide et zone principale contrainte. Le faux pictogramme CSS et le mot Tara separe des prototypes ne sont pas reproduits. Les composants gardent des dimensions stables afin que statuts, nombres, textes traduits et changements de progression ne deplacent pas brutalement la page.

### Creation

Choisir des MP3/OGG, afficher une ligne stable par piste, initialiser la personne depuis le nom, permettre la correction, saisir ou charger contexte et resumes anterieurs `.txt`/`.md`, puis lancer automatiquement des que toutes les validations sont terminees.

Les fichiers de texte chargent leur contenu dans la zone editable. Le navigateur ne calcule pas de compteur de tokens approximatif; il affiche les mesures et erreurs retournees par le backend.

### Upload

Afficher progression par piste et globale, etats de validation, reprise, remplacement et annulation. Le client demande l'offset serveur, calcule les SHA-256 avec Web Crypto et envoie les chunks sequentiellement, avec trois fichiers maximum en parallele selon la configuration publique.

### Suivi

Afficher les grandes etapes, la sous-etape, le pourcentage de l'operation courante et le pourcentage total. En attente, afficher position et estimation si disponible. Connecter SSE avec resynchronisation REST et polling de secours. La page change dynamiquement vers le resultat et affiche un toast discret si elle etait ouverte.

Traduire `concept_design_1.html` ainsi:

- titre de page adapte au statut, badges de statut et numero de tentative;
- progression totale principale, toujours identifiee comme telle;
- progression de l'operation courante integree a l'etape active, avec son propre pourcentage et libelle;
- chronologie verticale des sept etapes, etats `terminee`, `active`, `a venir`, `en attente`, `en retry`, `en erreur` et `annulee`;
- seule l'etape active affiche la sous-etape et les informations transitoires;
- panneau d'informations affichant heure de demarrage, estimation si fiable, retention et langue du job;
- actions `Copier le lien` et `Annuler` uniquement lorsqu'elles figurent dans `allowed_actions`;
- avertissement pres de la copie: toute personne possedant le lien peut consulter, annuler, relancer ou supprimer;
- rappel non bloquant que la page peut etre fermee et que le traitement continue;
- bascule sans rechargement complet vers le resultat terminal, puis toast accessible.

Ne jamais inventer une estimation pour remplir le panneau: afficher un etat neutre lorsque l'historique est insuffisant, le circuit est ouvert ou l'estimation est indisponible.

### Resultat et erreurs

Rendre le schema public en composants types, sans afficher le YAML brut. Afficher le cout approximatif uniquement sur la page resultat. Les erreurs offrent les actions autorisees: `Modifier et relancer`; pour un timeout eligible, aussi `Relancer a l'identique`.

Traduire `concept_design_2.html` ainsi:

- bandeau de resultat avec statut, expiration exacte et cout approximatif en euros a cinq decimales, ou etat partiel/indisponible explicite;
- `overview` rendu comme introduction non repliee et sans carte englobante;
- navigation laterale sticky sur desktop, generee depuis les sections publiques effectivement presentes;
- selecteur `Aller a` sur tablette/mobile, synchronise avec la section visible;
- sections repliees/depliees individuellement avec `Tout ouvrir` et `Tout fermer`;
- action `Copier la section` produisant titre et texte lisible, jamais le YAML brut;
- recherche locale a partir de deux caracteres, avec debounce court, nombre de correspondances, surlignage et ouverture des sections correspondantes;
- navigation entre correspondances et ancre de section accessibles, avec focus sur la cible;
- toast `aria-live` pour copie, erreur de presse-papiers et autres confirmations courtes;
- aucun export complet ou telechargement du YAML en V1.

Les titres et trois sections presentes dans le concept sont des exemples. `ResultNavigation`, `ResultSection` et `PublicBlockRenderer` parcourent le tableau canonique et supportent tous les `section_type` et blocs V1, y compris sections absentes, ordre variable et contenu long. Conformement au DESIGN, un `section_type` inconnu est rendu comme `generic` et journalise techniquement; un bloc inconnu est ignore sans casser la page. Aucun contenu de repli n'est interprete comme HTML ou Markdown.

Le fragment d'URL conserve le secret et peut aussi porter l'identifiant de section. Copier ce lien cible affiche l'avertissement de droits proprietaire; l'ouverture restaure la section, la developpe et place le focus. Les sections ouvertes et la position de lecture peuvent etre restaurees pendant la session navigateur sans stocker le resultat ni une seconde copie du secret.

Le bouton de copie et le bouton d'ouverture sont deux controles freres. Ne pas reproduire le `role="button"` imbrique dans le bouton de titre du prototype.

## Limites de reprise du design

- Conserver la structure, le rythme, la densite, les etats et le responsive des concepts.
- Ne pas figer dans cette tache les decorations de fond, gradients, ombres ou couleurs finales; elles appartiennent au chantier theme.
- Utiliser les icones Lucide du projet au lieu des SVG inline et caracteres symboliques des prototypes.
- Utiliser le logo officiel selon 07B; l'animation joue au plus une fois au chargement initial et devient immediatement statique en reduced motion.
- Ne pas coder en dur les textes, valeurs, heures, couts, dates, tentatives, sections ou pourcentages d'exemple.
- Respecter `prefers-reduced-motion`; aucune animation n'est necessaire pour comprendre un statut.

## Etat local et secrets

- Extraire le secret du fragment et le retirer de l'URL visible avec `history.replaceState` apres mise en memoire de session.
- Ne jamais stocker secret, contenu fichier ou resultat dans `localStorage`.
- Sauvegarder seulement les textes et metadonnees non sensibles du brouillon.
- Effacer le brouillon apres creation du job ou annulation volontaire.
- Ignorer tout snapshot ou evenement dont la revision est inferieure a l'etat courant.

## Internationalisation

Utiliser `react-i18next` des le depart. Le serveur fournit langue par defaut et langues valides; aucun selecteur n'est visible tant qu'une seule langue est disponible. Dates, nombres et euros utilisent `Intl`. Le job conserve sa langue propre.

## Securite

- Rendre toutes les donnees utilisateur et YAML par composants React textuels; interdire HTML brut, URL `javascript:` et style fourni par les donnees.
- Garder le secret uniquement en memoire de l'onglet, le retirer immediatement du fragment visible et le masquer dans erreurs, outils de telemetrie et traces reseau applicatives.
- N'enregistrer dans le brouillon local que des champs explicitement autorises; versionner sa forme et supprimer toute cle inconnue lors du chargement.
- Verifier type, taille et revision des snapshots/evenements avant mise en etat pour limiter prototype pollution, allocation excessive et contenu obsolescent.
- Ouvrir les eventuels liens externes avec une liste de protocoles autorises et `noopener`; aucune ressource externe n'est necessaire au runtime V1.
- Ne pas presenter un controle frontend comme une autorisation: le backend reste source de verite pour limites et `allowed_actions`.

## Validation

- Tests composants des etats vide, upload, attente, actif, retry, erreur, timeout, annulation, expiration et resultat.
- Aucun texte utilisateur n'est concatene comme HTML; rendu sans `dangerouslySetInnerHTML`.
- Navigation et actions principales utilisables au clavier; tests automatises axe/ARIA verts.
- Aucun chevauchement aux viewports mobile et desktop cibles.
- Une deconnexion SSE ou fermeture de page n'annule jamais le job.
- Le build ne contient aucun appel runtime vers un CDN.
- Des tests XSS utilisent noms de fichiers, personnes, contexte, erreurs et contenu YAML malveillants; aucun code ou HTML injecte n'est execute.
- Tests du suivi avec chaque etat d'etape, les deux pourcentages, estimation absente et changement dynamique vers le resultat.
- Tests du resultat avec chaque type de bloc, ordre/absence de sections, texte tres long, recherche, surlignage, tout ouvrir/fermer, copie et navigation par ancre.
- Tests du lien de section avec secret dans le fragment, avertissement de partage, restauration d'ouverture/focus et absence du secret dans les requetes serveur.
- Aucun controle interactif n'est imbrique dans un autre; l'ordre de tabulation et le focus apres navigation/copie sont verifies.
- La composition reste reconnaissable par rapport aux deux concepts aux viewports desktop, tablette et mobile, sans imposer une comparaison pixel-perfect du theme.
- Le logo officiel ne provoque aucun layout shift, ne rejoue pas pendant la navigation/SSE et conserve un fallback statique si l'animation echoue.

## Definition de fin

Le parcours complet est utilisable contre le faux runner depuis la selection des pistes jusqu'au resultat ou a la relance.
