# Exemples statiques des vues Tara

Cette galerie documente les états visuels principaux du frontend avec des
données fictives de type Lorem Ipsum. Elle ne contacte aucun backend, ne lit
aucun secret et n'est pas incluse dans le bundle Docker de production.

Ouvrir `index.html` dans un navigateur pour parcourir toutes les vues, ou
`view.html?view=job-running` pour ouvrir un exemple seul. Un petit serveur local
évite les restrictions propres aux URL `file://` :

```bash
python -m http.server 8090 --directory webinterface
```

La galerie est alors disponible sur
<http://127.0.0.1:8090/view-examples/>. La racine `webinterface` est nécessaire
pour servir également les styles et le logo réutilisés depuis `frontend/src`.

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
