# Resultat public 26.0.1

Le resultat public utilise `schema_name: tara.public_result` et `schema_version: 26.0.1`. Le contenu expose un titre et des sections ordonnees. Chaque section a un `section_id` stable, unique et compatible fragment URL (`[a-z][a-z0-9-]{0,63}`), un `section_type`, un titre et ses blocs.

Les types de section sont `overview`, `chronology`, `characters`, `quests`, `combat`, `locations`, `items`, `factions`, `uncertainties` et `generic`. Les blocs publics sont `paragraph`, `list`, `orderedList`, `keyValue`, `table` et `callout`.

La publication part d'un resultat interne avec une liste positive de champs. Les chemins et identifiants internes ne font pas partie du schema public. Le Markdown interne est transforme en paragraphes ou listes types pour alimenter les blocs structurés : les fences sont ignores, les titres et citations deviennent du texte, les liens et images gardent leur libelle sans URL, et emphase/code deviennent du texte simple. Une phrase narrative telle que «Le groupe suit une trace.» reste autorisee.

Depuis la publication du résumé exact, `content.summary_markdown` peut aussi
contenir la représentation bornée de `session_summary.md`. Ce champ est une
source publique destinée à la lecture et au téléchargement ; il reste distinct
des blocs structurés. L'API web le projette sous `summary_markdown`. Le frontend
sert le texte original au téléchargement et utilise un parseur Markdown sûr
uniquement pour l'affichage : pas de HTML brut, d'image distante ni de lien à
protocole dangereux.

Un document 26.0.1 forge est refuse s'il contient un chemin absolu ou du Markdown brut (fence, titre, citation, lien/image, emphase forte ou code inline) dans une valeur publique. Cette verification s'applique apres le chargement sur tous les titres et contenus de blocs.

Pour le contrat web Task08, `final.yaml` historique avec `schema_version: 1` et `summary.title` reste lisible via l'adaptateur explicite `final-v1`. Il est converti en resultat public 26.0.1 en memoire. Toute autre version est refusee.

```yaml
schema_name: tara.public_result
schema_version: 26.0.1
metadata:
  language: fr
  producer: tara
content:
  title: Session du 17 juillet
  sections:
    - section_id: overview-session
      section_type: overview
      title: Resume
      blocks:
        - type: paragraph
          text: Le groupe a atteint la tour.
        - type: list
          items: [Trouver la cle, Parler au gardien]
        - type: orderedList
          items: [Entrer, Explorer]
        - type: keyValue
          entries: [{key: Lieu, value: Tour}]
        - type: table
          headers: [Nom, Etat]
          rows: [[Ana, Presente]]
        - type: callout
          title: Incertitude
          text: Le nom du gardien reste a confirmer.
```

Adaptateur exact: `tara.public_result/1` accepte seulement `schema_version: 1` et `summary` avec `title`, `sections` et `scenes` connus. Une section v1 supportee contient seulement `section_id` optionnel, `title` et `content`; `scenes` doit etre vide car aucune forme Task08 n'etait consommee. Les IDs historiques sont normalises dans cet ordre: `resume_express`, `executive_summary`, `impacts`, `key_points`, `final_state`, puis les IDs metier canoniques. Les chemins, prompts, traces, IDs internes et syntaxe Markdown restent hors du resultat public.
