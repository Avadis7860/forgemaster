# docs/ — mémoire durable du projet

La **prose** du projet : intention, architecture, décisions, specs. C'est ce qui survit aux sessions — une
session qui reprend le projet lit `docs/` **avant** de toucher au code.

## Contenu

- **`architecture.md`** — point d'entrée : intention (ce que le projet fait, pour qui, critère de succès),
  où vit quoi, comment le projet se travaille. À étoffer au fil du travail.
- Les autres notes (décisions, specs) s'ajoutent ici au fur et à mesure, en markdown.

## Interroger, ne pas tout relire

`docs/` est indexé par **docsmap**. Cherche par intention plutôt que d'ouvrir chaque fichier :

```
docsmap where "<intention>"
```

Après avoir touché `docs/`, régénère l'index : `docsmap build && docsmap check` (l'anti-archéologie en
dépend). Le skill `docs-authoring` (`.claude/skills/`) porte la convention d'écriture.
