# tests/

Stratégie de la phase structure : prouver que **la spine tient** pendant qu'on porte les couches.

- `test_skeleton.py` — import du package ; **socle fonctionnel** (config résout les 3 modes ; `core.run`
  exécute une commande locale ; `core.ids`/`core.fs` valident/bornent ; `db` crée les 4 tables) ; le parser
  câble toutes les sous-commandes ; `cockpit --help` répond ; `daemon.app` s'importe **sans** fastapi
  (imports paresseux).

## Conventions

- **Fixtures minuscules à noms fictifs** — jamais un vrai basename de projet (un vrai nom polluerait un
  graphe si scanné ; leçon vault `test_literals_pollute_script_graph`). Les slugs de test sont fictifs
  (`ma-feature`, `demo-project`).
- Déterminisme d'abord : toute I/O des couches (exécution locale, git) est **injectable** → testable
  hors-live. Un test qui n'existe pas pour une capacité livrée = capacité non livrée.
- À mesure que les couches sont portées : un test par module (correction sur fixture + invariants de la
  spec correspondante, cf. `docs/specs/`).
