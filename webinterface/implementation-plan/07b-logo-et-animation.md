# Etape 07B - Logo Tara et animation du D20

## Objectif

Integrer les logos presentes dans `webinterface/logo_animations.html` comme assets officiels de la webinterface, avec une version statique pour l'usage courant et une animation courte du D20 pour l'entree initiale dans l'application.

L'animation ne doit pas devenir une distraction permanente, provoquer un decalage de layout ou augmenter inutilement le cout de rendu de chaque page.

## Dependances

Etapes 01, 07 et 07A.

## Sources approuvees

- `webinterface/logo-final/tara-logo-black-transparent.png`: texte noir, fond alpha, 1586 x 992, RGBA.
- `webinterface/logo-final/tara-logo-white-transparent.png`: texte blanc, fond alpha, 1586 x 992, RGBA.
- `webinterface/logo_animations.html`: reference de composition et de mouvement.

`tara-logo-no-yellow.png` n'est pas un asset web retenu: il ne possede pas de canal alpha et sa taille est nettement superieure. Le conserver comme source de travail uniquement tant qu'il est utile au chantier de design.

## Decisions d'usage

- Utiliser la version noire statique dans l'en-tete clair de toutes les pages V1.
- Utiliser la version blanche uniquement sur une surface sombre explicitement validee; ne pas l'afficher sur le theme clair principal.
- Le logo de l'en-tete est statique apres l'initialisation. Il ne rejoue pas a chaque navigation React, mise a jour SSE ou changement d'etape.
- L'animation du D20 peut jouer une seule fois pendant le premier chargement applicatif, si celui-ci dure assez longtemps pour afficher un etat de chargement sans flash.
- Ne pas ajouter d'ecran splash artificiellement long: si le shell est pret en moins de 250 ms, afficher directement le logo statique.
- L'animation joue au maximum un cycle par session d'onglet. Une cle en memoire suffit; ne pas stocker cette preference dans `localStorage`.
- Ne pas afficher de bouton `Relancer` dans l'application V1. Ce controle appartient uniquement a la page de demonstration.
- Avec `prefers-reduced-motion: reduce`, economie de donnees activee ou animation non chargee, afficher immediatement le logo statique final.
- Une erreur de chargement de l'animation ne bloque jamais l'application et revient au PNG statique.

## Fichiers a creer ou modifier

- `webinterface/frontend/src/components/brand/TaraLogo.tsx`.
- `webinterface/frontend/src/components/brand/AnimatedTaraLogo.tsx`.
- `webinterface/frontend/src/components/brand/BrandLink.tsx`.
- `webinterface/frontend/src/components/brand/logo.css`.
- tests composants associes.

`TaraLogo` accepte uniquement des props controlees. Tant qu'aucune surface sombre n'utilise la variante blanche en V1, le composant runtime expose uniquement `black` afin que Vite n'embarque pas l'asset blanc inutilise. Le type pourra etre elargi a `black | white` au moment ou une surface sombre approuvee sera effectivement livree:

- `variant: "black"` en V1, puis `"black" | "white"` lorsqu'une surface sombre l'exige;
- `size: "header" | "loading" | "standalone"`;
- `decorative: boolean`;
- classes internes controlees, sans URL d'image fournie par l'appelant.

`AnimatedTaraLogo` accepte `variant`, `size`, `play` et `onAnimationEnd`. Il compose une base statique, un masque du D20 original et une copie decoupee du D20 mobile. Il n'expose pas les coordonnees comme donnees utilisateur.

`BrandLink` fournit l'unique nom accessible `Tara - accueil`. Les images internes ont alors `alt=""`; un logo autonome hors lien utilise `alt="Tara"`.

## Pipeline des assets

Les PNG sources ont une grande toile transparente. Generer pendant le developpement des derives web recadres sur les bornes alpha, sans modifier les originaux:

- logo horizontal noir 1x et 2x pour en-tete;
- logo horizontal blanc 1x et 2x;
- version de chargement plus grande si necessaire;
- icone livre+D20 recadree pour favicon et icones applicatives, sans texte minuscule illisible.

Contraintes:

- conserver un format avec alpha, PNG optimise ou WebP lossless apres comparaison visuelle;
- definir `width`, `height` et `aspect-ratio` dans le markup pour eviter le layout shift;
- utiliser des noms d'assets avec empreinte via Vite;
- ne pas embarquer les deux variantes et toutes les resolutions si elles ne sont pas utilisees par le chemin courant;
- precharger uniquement le logo statique noir du shell;
- charger la couche animee de maniere differee;
- ne pas convertir les images en longues data URLs dans le bundle JavaScript;
- documenter le script reproductible d'optimisation et verifier que le resultat garde un canal alpha.

Fichiers cibles possibles:

- `webinterface/frontend/src/assets/brand/tara-logo-black.png`;
- `webinterface/frontend/src/assets/brand/tara-logo-black@2x.png`;
- `webinterface/frontend/src/assets/brand/tara-logo-white.png`;
- `webinterface/frontend/src/assets/brand/tara-logo-white@2x.png`;
- `webinterface/frontend/public/favicon.ico` et icones PNG necessaires;
- `scripts/build_web_brand_assets.py` ou outil equivalent utilisant une bibliotheque d'image deja approuvee.

## Dimensions d'affichage

Le logo horizontal conserve son ratio recadre et ne doit jamais etre etire.

- en-tete desktop: boite maximale 132 x 44 px;
- en-tete mobile: boite maximale 112 x 38 px;
- chargement initial desktop: largeur 260-320 px;
- chargement initial mobile: largeur maximale `min(260px, 72vw)`;
- espace reserve avant chargement de l'image;
- `object-fit: contain`; aucun crop CSS du texte ou du livre.

Verifier les dimensions sur l'asset recadre, pas sur la toile source 1586 x 992 qui contient de grandes marges transparentes.

## Animation retenue

Le livre, ses lignes et le nom Tara restent immobiles. Seul le D20 apparait au-dessus de sa position, descend, depasse legerement, corrige sa rotation et se stabilise.

Courbe de reference: `cubic-bezier(.2, .78, .25, 1)`. Duree maximale: 4,8 s, mais en production le mouvement utile peut etre raccourci entre 1,8 et 2,4 s apres validation visuelle. Il ne boucle pas.

Keyframes de reference adaptes pour finir dans l'etat visible:

```css
@keyframes tara-d20-settle {
  0%, 8% {
    opacity: 0;
    transform: translateY(-16%) rotate(-130deg) scale(.72);
  }
  12% { opacity: 1; }
  38% {
    opacity: 1;
    transform: translateY(1.8%) rotate(10deg) scale(1.045);
  }
  48% {
    opacity: 1;
    transform: translateY(-.7%) rotate(-4deg) scale(.985);
  }
  57%, 100% {
    opacity: 1;
    transform: translateY(0) rotate(0) scale(1);
  }
}
```

Contrairement a la demonstration, ne pas remettre l'opacite a zero entre 94 et 100 %, car l'application ne boucle pas l'animation.

## Masques et coordonnees

Si les PNG sources complets sont utilises pour les couches animees, conserver les coordonnees de reference:

Version noire:

```css
--dice-clip: polygon(32.1% 37.8%, 38.35% 43.8%, 38.35% 55.25%, 32.1% 61.65%, 25.8% 55.25%, 25.8% 43.8%);
--origin-x: 32.1%;
--origin-y: 49.7%;
--mask-left: 25.65%;
--mask-top: 37.55%;
--mask-width: 12.9%;
--mask-height: 24.35%;
```

Version blanche:

```css
--dice-clip: polygon(35.4% 39.15%, 40.75% 44.15%, 40.75% 54.5%, 35.4% 59.55%, 30.05% 54.5%, 30.05% 44.15%);
--origin-x: 35.4%;
--origin-y: 49.35%;
--mask-left: 29.9%;
--mask-top: 38.9%;
--mask-width: 11%;
--mask-height: 20.9%;
```

Si les assets sont recadres ou si le D20 devient un asset separe, recalculer ces coordonnees dans le script de generation et les verrouiller par tests visuels. Ne pas corriger les valeurs manuellement dans plusieurs fichiers CSS.

## Integration dans le shell

- Remplacer le faux pictogramme et le texte Tara construits en CSS dans les concepts 1 et 2 par `BrandLink`.
- Conserver la hauteur d'en-tete 72/64 px de la specification 07A.
- Le logo ne doit pas agrandir l'en-tete, modifier la position du lien Aide ou provoquer de layout shift.
- Sur ecran etroit, reduire le logo horizontal dans sa boite; ne pas masquer arbitrairement le mot Tara.
- Le lien de marque revient a l'ecran de creation ou a la destination autorisee par le parcours, sans perdre un brouillon de maniere silencieuse.

## Etat de chargement initial

1. Reserver la boite du logo statique des le HTML du shell.
2. Afficher le logo statique immediatement.
3. Si le chargement applicatif depasse 250 ms, si le mouvement est autorise et si l'animation n'a pas joue dans l'onglet, charger les couches animees.
4. Lancer une seule animation et marquer la session en memoire.
5. A `animationend`, garder exactement l'etat final et liberer `will-change`.
6. Si l'application devient prete pendant l'animation, ne pas retarder la navigation; terminer ou remplacer par le logo statique sans flash.

## Reduced motion et economie de ressources

```css
@media (prefers-reduced-motion: reduce) {
  .tara-logo__d20 {
    opacity: 1;
    animation: none;
    transform: none;
  }
}
```

- Aucun smooth scroll ou mouvement alternatif ne remplace l'animation.
- Retirer `will-change` hors animation.
- Suspendre l'animation si le document devient cache avant son debut; ne pas la rejouer au retour.
- Ne pas utiliser canvas, video, GIF ou bibliotheque d'animation pour ce mouvement CSS simple.

## Accessibilite

- Une marque cliquable possede un seul nom accessible; ne pas annoncer separement base, masque et D20.
- Les couches d'animation sont `aria-hidden="true"` et decoratives.
- Le mouvement ne code aucun statut metier et aucune information n'est perdue lorsqu'il est desactive.
- Pas de clignotement, variation lumineuse rapide ou mouvement permanent.
- Le logo noir/blanc est choisi selon le contraste reel du fond; ne pas inverser automatiquement via filtre CSS.
- Le focus appartient au lien englobant et respecte la bague de focus de 07A.

## Performance

- Budget initial recommande du logo statique recadre: moins de 50 KiB par resolution utilisee.
- Budget additionnel de l'animation: moins de 100 KiB compresses si une couche D20 separee est generee; sinon mesurer le cout des deux decodages.
- Aucun layout shift attribuable au logo (`CLS` cible 0 pour le composant).
- Aucun long task JavaScript: l'animation repose sur `transform` et `opacity`.
- Ne pas precharger la version blanche sur le theme clair.
- L'image de demonstration 1586 x 992 ne doit pas etre decodee pour un logo d'en-tete si un derive adapte existe.

## Securite

- Assets locaux uniquement, sans URL configurable, CDN, SVG distant ou contenu utilisateur.
- Verifier type PNG/WebP, dimensions, canal alpha et empreinte pendant la generation.
- Exclure metadata inutile des derives et ne jamais incorporer de chemin local ou commentaire sensible.
- Ne pas utiliser de SVG arbitraire ou HTML injecte pour reconstruire le logo.
- CSP `img-src 'self'` compatible; aucun `data:` necessaire si les images restent des fichiers.
- `logo_animations.html` et les sources de travail ne sont pas servis par FastAPI en production ni copies dans l'image Docker runtime.

## Validation

- Test composant des variantes noire/blanche, tailles et modes decoratif/autonome.
- Screenshot du logo noir sur surface claire et blanc sur surface sombre.
- Screenshot de l'en-tete a 1440, 980, 390 et 320 px sans collision avec Aide.
- Verification alpha: aucun rectangle de fond integre dans les derives.
- Verification ratio: aucune deformation ou crop du mot Tara/livre.
- Animation testee au debut, depassement, correction et etat final; une seule iteration.
- `prefers-reduced-motion` affiche directement l'etat final et aucune animation calculee.
- Navigation React et mises a jour SSE ne rejouent pas le mouvement.
- Erreur de chargement d'une couche animee: fallback statique immediat.
- Budgets d'octets, absence de CLS et absence de long task verifies.
- Build Vite et image Docker contiennent uniquement les derives necessaires, pas la galerie HTML ni `tara-logo-no-yellow.png`.

## Definition de fin

Le logo officiel est utilise de maniere coherente dans le shell, l'animation du D20 est ponctuelle, accessible et performante, et les variantes statiques restent toujours disponibles comme fallback.
