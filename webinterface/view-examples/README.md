# Laboratoire visuel et exemples statiques de Tara

Cette galerie réunit cinq directions de design interactives et documente les
états visuels principaux du frontend. Elle ne contacte aucun backend, ne lit
aucun secret et n'est pas incluse dans le bundle Docker de production.

Les concepts utilisent un scénario de jeu de rôle entièrement fictif pour
permettre une comparaison réaliste. Les onze vues fonctionnelles historiques
conservent leurs données génériques.

Dans l’état `prepare`, chaque concept représente séparément le contexte
supplémentaire et les résumés antérieurs, conformément au parcours produit.

Ouvrir `index.html` dans un navigateur pour parcourir toutes les vues, ou
`view.html?view=job-running` pour ouvrir un exemple seul. Un petit serveur local
évite les restrictions propres aux URL `file://` :

```bash
python -m http.server 8090 --directory webinterface
```

La galerie est alors disponible sur
<http://127.0.0.1:8090/view-examples/>. La racine `webinterface` est nécessaire
pour servir également les styles et le logo réutilisés depuis `frontend/src`.

## Directions proposées

| Clé | Direction | Intention |
|---|---|---|
| `editorial` | Édition Azur | lecture longue, calme et pérennité |
| `nocturne` | Tara Nocturne | immersion, technologie et progression |
| `studio` | Studio Courant | manipulation directe et convivialité |
| `atlas` | Atlas narratif | cartographie, densité et précision |
| `prism` | Prisme calme | transparence, sérénité et qualité premium |

Ouvrir une direction seule avec
`concept.html?theme=editorial&view=progress`. Les vues disponibles sont
`prepare`, `progress` et `result`.

L'audit détaillé, la matrice de choix et le plan d'intégration sont disponibles
dans [`DESIGN-AUDIT.md`](DESIGN-AUDIT.md).

Les exemples réutilisent les feuilles de style et le logo du frontend, mais le
HTML est volontairement statique. Lorsqu'une vue de production change, mettre
à jour l'entrée correspondante dans `examples.js` et conserver uniquement des
données publiques fictives.

## Couverture

| Clé | Vue représentée |
|---|---|
| `loading` | chargement initial |
| `new-audio` | création depuis des pistes audio |
| `new-merged` | création depuis une transcription YAML fusionnée |
| `new-zip` | création depuis une archive ZIP |
| `help` | aide intégrée |
| `upload-audio` | transfert parallèle de pistes audio |
| `upload-merged` | validation d'une transcription fusionnée |
| `upload-zip` | extraction et association des pistes d'un ZIP |
| `job-running` | suivi d'une analyse en cours |
| `job-failed` | erreur terminale et actions de relance |
| `result-markdown` | résultat et rendu HTML de `session_summary.md` |
