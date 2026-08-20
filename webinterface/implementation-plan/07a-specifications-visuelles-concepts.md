# Etape 07A - Specifications visuelles issues des concepts

## Objectif

Transformer `concept_design_1.html` et `concept_design_2.html` en specifications visuelles mesurables pour l'implementation React. Ce document fixe la baseline V1 de composition et de style des pages de suivi et de resultat, sans transformer les prototypes HTML en dependance de production.

Le chantier de theme pourra faire evoluer ces valeurs plus tard. Tant qu'une nouvelle direction n'est pas validee, ce document est la reference de l'implementation et des tests visuels.

## Dependances

Etape 07 et contrats des etapes 06, 09 et 15.

## Sources et arbitrage

- `webinterface/concept_design_1.html`: suivi d'un job.
- `webinterface/concept_design_2.html`: resultat termine.
- `webinterface/DESIGN.md`: prioritaire pour comportements, donnees, securite et accessibilite.

Adaptations de production obligatoires:

- utiliser des tailles de police fixes par breakpoint, jamais une formule en `vw`;
- utiliser `letter-spacing: 0` partout;
- limiter a 8 px le rayon des cartes, panneaux, champs et boutons rectangulaires; conserver les cercles/pastilles totalement arrondis;
- supprimer les grands halos radiaux, orbes et motifs decoratifs animes;
- utiliser Lucide plutot que les SVG inline ou caracteres symboliques des prototypes;
- separer le bouton de copie du bouton d'accordeon;
- limiter les gradients aux indicateurs fonctionnels discrets.

## Fichiers a creer ou modifier

- `webinterface/frontend/src/styles/tokens.css`, `base.css`, `layout.css`.
- styles de composants du suivi et du resultat.
- `webinterface/frontend/src/assets/fonts/` si Inter est embarquee localement.
- `webinterface/frontend/e2e/concepts/` pour screenshots et regressions visuelles.
- `webinterface/implementation-plan/07b-logo-et-animation.md` pour les dimensions, derives et mouvements de la marque Tara.

## Palette de production

| Token | Valeur initiale | Usage |
|---|---:|---|
| `--color-page` | `#f6f8fc` | fond plat principal |
| `--color-surface` | `#ffffff` | panneaux et champs |
| `--color-text` | `#151a28` | texte principal |
| `--color-muted` | `#6f7688` | texte secondaire |
| `--color-border` | `#dce2ee` | separateurs et contours |
| `--color-track` | `#e8ecf4` | piste de progression |
| `--color-primary` | `#3d5afe` | action, focus, progression |
| `--color-secondary` | `#18a7c8` | accent secondaire fonctionnel |
| `--color-accent` | `#7657d6` | accent ponctuel uniquement |
| `--color-success` | `#287a57` | termine/validation positive |
| `--color-danger` | `#c84646` | erreur/action destructive |
| `--color-warning` | `#aa6d16` | warning non bloquant |
| `--color-success-soft` | `#eef8f3` | fond positif |
| `--color-accent-soft` | `#f4f1fb` | callout neutre/incertitude |

Regles:

- fond de page uni, sans pseudo-element radial;
- surfaces majoritairement blanches/neutres afin d'eviter une interface monochrome bleue;
- vert reserve au succes, rouge aux erreurs/destructions, ambre aux avertissements;
- violet limite aux petits accents et jamais seul porteur d'information;
- mesurer chaque paire texte/fond: viser WCAG AA, 4,5:1 pour texte normal et 3:1 pour grand texte/controles;
- chaque etat combine couleur avec icone, texte ou forme.

## Typographie

Famille cible: `Inter` embarquee localement en variable font si possible. Fallback: `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. Aucun CDN.

| Role | Desktop | Mobile | Poids | Interligne |
|---|---:|---:|---:|---:|
| Marque Tara | 28 px | 24 px | 700 | 1.15 |
| H1 suivi | 48 px | 38 px | 620 | 1.08 |
| H1 resultat | 40 px | 32 px | 650 | 1.08 |
| H2 introduction | 36 px | 29 px | 650 | 1.15 |
| Titre section | 28 px | 22 px | 620 | 1.2 |
| Titre panneau | 19 px | 18 px | 640 | 1.3 |
| Texte introduction | 18 px | 16 px | 400 | 1.7 |
| Texte standard | 16 px | 16 px | 400 | 1.55 |
| Etape | 18 px | 16 px | 620 | 1.3 |
| Sous-etape | 15 px | 14 px | 400 | 1.45 |
| Bouton/navigation | 15-16 px | 15-16 px | 550-650 | 1.25 |
| Label/metadonnee | 12-13 px | 12-13 px | 600 | 1.35 |
| Pourcentage total | 48 px | 34 px | 650 | 1 |
| Cout | 26 px | 20 px | 650 | 1.15 |

Toutes les tailles sont fixes dans les media queries. Aucun letter spacing negatif ou positif, y compris sur titres et labels en capitales.

## Espacement et elevation

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-7: 28px;
--space-8: 32px;
--space-10: 40px;
--space-12: 48px;
--space-16: 64px;
```

- rayon standard 8 px; petit rayon 4 px; pastilles/cercle `999px`;
- ombre panneau `0 12px 36px rgb(37 48 78 / 8%)`;
- ombre toolbar/toast `0 10px 28px rgb(37 48 78 / 10%)`;
- focus equivalent a `0 0 0 4px rgb(61 90 254 / 16%)`, avec bord primaire si necessaire;
- aucun empilement de cartes: sections non encadrees ou separees par lignes; seuls panneau d'information, bandeau, toolbar et callouts sont cadres;
- controles interactifs de 44 px minimum; actions du panneau de suivi jusqu'a 56 px;
- aucun hover/focus ne change dimensions, bordures ou grille.

## Shell commun

- en-tete 72 px desktop/tablette, 64 px sous 680 px;
- padding horizontal 54 px au grand desktop, 24 px desktop compact, 18 px mobile;
- logo Tara horizontal officiel dans une boite maximale 132 x 44 px; mobile 112 x 38 px; conserver le ratio recadre selon 07B;
- cible Aide 44 x 44 px; texte masque sous 680 px mais nom accessible conserve;
- page maximale 1480 px; gouttiere 54 px grand desktop, 20 px tablette, 14-16 px mobile;
- largeur minimale 320 px;
- contenu a 34-42 px sous l'en-tete desktop et 20-30 px mobile;
- fond `--color-page`, sans illustration, halo ou orbite.
- animation du D20 reservee au chargement initial; logo d'en-tete statique pendant l'usage courant.

## Page de suivi

### En-tete et statuts

- H1 sur une ligne si possible, retour a la ligne sans chevauchement;
- badges a 22 px sous le H1, gap 14 px;
- badge hauteur 38 px, padding horizontal 14 px, rayon 8 px;
- indicateur actif 10 px, pulse 2,4 s maximum, desactive en reduced motion.

### Double progression

Progression totale:

- bloc a 36 px sous les badges; libelle 18 px et marge basse 16 px;
- grille `minmax(0, 1fr) 120px`, gap 28 px, largeur max 1120 px;
- piste 13 px totalement arrondie;
- remplissage bleu vers cyan, sans violet dominant ni shimmer obligatoire;
- valeur 48 px desktop/34 px mobile, reserve stable pour `100 %`.

Progression courante:

- sous la sous-etape active, pas comme seconde grande barre concurrente;
- libelle 13-14 px, piste 6 px, valeur 14 px;
- alignee au texte de l'etape;
- si aucune mesure n'existe, afficher le libelle d'etape et un etat neutre sans pourcentage invente.

### Etapes et panneau

- desktop: grille `minmax(440px, 1fr) minmax(380px, 460px)`, gap cible 64 px, maximum 96 px;
- marge haute 34 px; passage a une colonne a 980 px, page max 760 px, gap 30 px;
- chronologie max 600 px; ligne 67 px minimum, active 76 px;
- colonne de noeuds 72 px desktop/58 px mobile;
- titre etape 18/16 px; detail 15/14 px;
- termine: cercle vert+check; actif: anneau primaire; futur: noeud neutre+liaison pointillee;
- ne pas reprendre la planete/orbite complexe si elle nuit a la lecture.

Panneau:

- padding 26 x 28 px desktop, 22 x 18 px mobile;
- bord 1 px, rayon 8 px, surface blanche, ombre panneau;
- details gap 22 px; grille icone 22-24 px, libelle flexible, valeur auto;
- mobile: valeur sous le libelle;
- actions en colonne, gap 12 px, hauteur 56 px;
- copie en bleu; annulation en rouge sans grand fond rouge permanent.

Notice:

- marge haute 28-32 px, hauteur minimum 86 px desktop;
- deux informations et un lien, puis une colonne sous 980 px;
- padding 18 x 28 px desktop, 18 px mobile; texte 14-15 px.

## Page de resultat

### Bandeau

- grille desktop `auto minmax(0, 1fr) auto`, gap 24 px;
- hauteur minimum 148 px, padding 25 x 32 px, rayon 8 px;
- fond succes tres discret vers blanc si un gradient est conserve; aucun motif radial anime;
- sceau succes statique 72-82 px avec icone Check Lucide;
- H1 40/32 px; metadata 15 px avec wrap et gap 11 x 17 px;
- cout: colonne min 215 px, separateur gauche, alignement droit, valeur 26 px/label 13 px;
- bouton info dans une cible 44 px;
- sous 980 px: deux colonnes et cout inline sous le texte;
- sous 680 px: une colonne centree, padding 24 x 20 px.

### Navigation et contenu

- desktop: navigation 232 px + contenu, gap 48-76 px, marge haute 28 px;
- navigation sticky a 24 px, liens hauteur 44 px, gap 5 px;
- actif: texte primaire, fond primaire 8 %, barre laterale 3 px;
- note publique 12 px/1.55, padding 16 px, rayon 8 px;
- a 980 px: navigation laterale masquee et selecteur `Aller a` pleine largeur;
- selecteur hauteur 42 px, rayon 8 px;
- introduction largeur de lecture max 820 px; texte 18/16 px et interligne 1.7.

### Toolbar

- sticky a 16 px desktop, statique sous 680 px;
- grille `minmax(260px, 1fr) auto auto`, gap 12 px;
- hauteur minimum 68 px, padding 10 x 12 px, rayon 8 px;
- recherche hauteur 46 px, icone a gauche et compteur a droite;
- boutons hauteur 44 px;
- mobile: recherche premiere ligne, deux boutons de meme largeur en dessous;
- utiliser `scroll-margin-top` pour que la toolbar ne masque pas une section ciblee.

### Sections

- `overview` non encadree, padding vertical 38/30 px, titre 36/29 px;
- en-tete de section minimum 74 px avec numero, titre, copie et chevron;
- numero 13 px, titre 28/22 px, controles dans des cibles 44 px;
- copie et ouverture sont deux boutons freres;
- contenu ouvert: padding gauche 43 px desktop/4 px mobile, bas 34 px;
- separateurs simples, aucune carte autour de chaque section;
- texte 16 px/1.65;
- tableaux complets dans une zone a scroll horizontal sur mobile;
- surlignage recherche `#dce7ff`, rayon 3 px, sans changer l'interligne.

## Responsive

| Largeur | Suivi | Resultat |
|---:|---|---|
| `> 980px` | deux colonnes etapes/panneau | nav 232 px + contenu, toolbar sticky |
| `681-980px` | une colonne, max 760 px | selecteur mobile, bandeau deux colonnes |
| `601-680px` | une colonne compacte | bandeau une colonne, toolbar deux lignes |
| `320-600px` | gouttiere 16 px, noeuds 58 px | sections et key/value empiles |

Tester exactement `981`, `980`, `681`, `680`, `601`, `600`, `390` et `320` px pour verifier les sauts.

## Etats visuels obligatoires

- controles: `default`, `hover`, `focus-visible`, `active`, `disabled`, `loading`;
- job: `queued`, `running`, `retry`, `degraded`, `cancel_requested`, `cancelled`, `failed`, `timed_out`, `completed`;
- etape: terminee, active mesurable, active sans mesure, future, erreur;
- estimation: disponible, indisponible, suspendue;
- cout: complet, partiel, indisponible;
- section: ouverte, fermee, ciblee par URL, avec correspondance;
- recherche: vide, courte, aucun resultat, resultats, saisie longue;
- toast: succes et erreur non bloquante.

Les textes longs passent a la ligne. Aucun ellipsis ne masque une information metier essentielle.

## Mouvement

- transitions 160-220 ms pour couleur, opacite et transform;
- accordions adaptes a la hauteur reelle, sans `max-height` arbitraire;
- pulse lent possible pour le statut, mais aucun shimmer permanent requis;
- aucune orbite, contour respirant ou fond en mouvement;
- avec `prefers-reduced-motion: reduce`, supprimer animation, smooth scroll et transitions non essentielles.

## Accessibilite visuelle

- focus visible avec contraste 3:1 minimum; cible tactile 44 x 44 px;
- zoom 200 % sans perte de contenu/action;
- les deux progressions ont nom et valeurs ARIA distincts;
- etape active avec `aria-current="step"`;
- accordions avec `aria-expanded` et `aria-controls`;
- recherche/toasts dans une region `aria-live` adaptee;
- icones decoratives masquees; boutons icones nommes et avec tooltip si necessaire;
- aucun texte important dans une image ou pseudo-element.

## Algorithme de rendu stable

1. Reserver grilles et dimensions minimales avant les donnees.
2. Formatter date, cout et pourcentage avant rendu pour anticiper la largeur maximale.
3. Utiliser `minmax(0, 1fr)` sur les colonnes textuelles.
4. Autoriser le retour a la ligne des titres et valeurs.
5. Empiler lorsqu'une colonne n'atteint plus sa largeur minimale.
6. Ne jamais reduire la police avec une formule liee au viewport.
7. Appliquer `overflow-wrap: anywhere` aux URLs/mots exceptionnellement longs.

## Validation visuelle

Screenshots Playwright:

- `1440 x 900`, `980 x 900`, `680 x 900`, `390 x 844`, `320 x 720`;
- suivi actif, attente, retry, erreur et termine avant bascule;
- resultat court, complet, tres long et recherche active;
- cout complet/partiel/indisponible et estimation disponible/indisponible;
- francais avec les chaines les plus longues;
- reduced motion et zoom 200 % pour controles critiques.

Assertions:

- aucun scroll horizontal de page a partir de 320 px;
- aucune intersection entre controles/textes critiques;
- aucun contenu masque par la toolbar sticky;
- dimensions identiques avant/apres hover/focus;
- contraste automatise puis verification manuelle des etats;
- comparaison contre la baseline React approuvee, pas contre les pixels exacts des prototypes.

## Securite

- Aucun style, classe, URL d'asset ou HTML ne vient du contenu YAML/utilisateur.
- Couleurs et variantes choisies par enums internes controles.
- URLs HTTP/HTTPS rendues par un composant dedie avec protocoles autorises et `rel="noopener noreferrer"`.
- Les prototypes ne sont pas servis par FastAPI et ne sont pas copies dans l'image runtime.
- Screenshots/fixtures sans secret ni donnee utilisateur reelle.

## Validation

- Tokens centralises, sans palette parallele injustifiee.
- Grilles, dimensions et breakpoints respectes.
- Adaptations visibles: deux progressions, rayons 8 px, texte sans letter spacing, fond sans orbes, controles separes, icones Lucide.
- Tests responsive, clavier, contraste, reduced motion, zoom et contenu long verts.
- Aucune section ou valeur d'exemple des concepts codee en dur.

## Definition de fin

Les pages React de suivi et de resultat possedent une baseline visuelle approuvee, mesurable et testee sur tous les viewports cibles, tout en restant separables d'un futur changement de theme.
