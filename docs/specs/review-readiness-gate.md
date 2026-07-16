# spec — review-readiness gate : quand dispatcher le reviewer Tier-1

> Cible : `dispatch/reviewer.py::_readiness` + `dispatch_reviewer` (readiness → dispatch fail-closed →
> verdict SHA-bound). Répond au **quand reviewer**, jamais au **quel ordre** (ça, c'est le DAG). Frère amont
> de `gate/review.py` (le verdict) et distinct de `gate/toolchain.py` (Tier-0 déterministe, autre question).

## Problème tranché

Dispatcher un review-worker (`claude -p` Tier-1 sémantique) sur une branche dont le worker de feature n'a
**pas fini son travail** produit des **faux-positifs** : le reviewer flague du code qu'une phase encore à
venir aurait câblé. Coût réel (cf. `feedback-no-bandaid-on-flawed-check`) → il faut **gater à la source**, pas
filtrer le verdict après coup. Le check doit être **générique** (indépendant du type de projet) : aucune
notion de `design.md`, aucune heuristique métier.

## Règles verrouillées

1. **Le signal de complétude générique = la terminalité des statuts de task.** Une feature est *prête pour la
   review* ssi elle a ≥1 task ET **toutes** ses tasks sont dans un état taskmap **terminal** (`DONE` ou
   `CANCELLED`). Toute task en `READY`/`BLOCKED_DEPS`/`ACTIVE`/`BLOCKED`/`ERROR`/`CYCLE` → il reste du travail
   ou c'est coincé → **hold** honnête avec la raison (quelle(s) task(s), quel état).

2. **Une seule autorité de séquencement.** L'état vient de `resolver.classify` — le **même moteur** que le DAG
   `depends_on` (cf. `docs/specs/task-next-resolver-dag.md`). Le gate ne maintient pas sa propre notion de
   « task faite » : il dérive de la classification taskmap. Conséquence directe : une task `blocked` (état
   `BLOCKED`, non terminal) tient la review — l'ancien check ad-hoc `status ∈ {todo, in_progress}` la laissait
   passer (une task `blocked` n'est ni l'un ni l'autre → faussement « complète »). Corrigé le 2026-07-16.

3. **Pas d'heuristique métier, pas de parsing de phases METHOD, pas de checklist d'acceptance.** Les phases
   METHOD sont de la prose injectée dans le prompt, pas un état machine ; l'`acceptance` d'une task est un
   **cadrage** (de quoi parle la feature), jamais une checklist à cocher — le reviewer le lit comme contexte,
   pas comme critère de complétude. La complétude/le séquencement appartiennent au DAG et au **pipeline de
   lancement** (cf. `cockpit-launch-pipeline` : le socle/design avant le code est porté par une task de socle
   dont la feature-code `depends_on`, pas par ce gate). Mettre « design.md non vide » ici casserait la
   généricité (footgun identifié 2026-07-15).

4. **Dispatch fail-closed.** `dispatch_reviewer` consomme la readiness AVANT tout spawn : hold si tasks
   inachevées, branche absente (feature jamais dispatchée), diff vide, worktree absent, ou verdict déjà frais
   (idempotent). Jamais de run silencieux sur une branche inachevée → jamais de verdict prématuré.

5. **Le verdict est SHA-bound.** Une fois prête, la review écrit son verdict via `gate/review.write_verdict`
   (contrat `review-gate-v2`, garde `evidence⊂diff`), ancré au HEAD reviewé. Consommé par `gate/merge`
   comme cran Tier-1 (fresh + 0 🔴, human-overridable).

## Articulation (ce que ce gate n'est PAS)

- **≠ Tier-0 `gate/toolchain`** (ruff/mypy/pytest/`npm run gate`) : déterministe, prouve que le code
  compile/passe — un Tier-0 vert ne prouve **pas** que les phases de travail sont finies.
- **≠ ordre inter-feature** : porté par le DAG `depends_on` des features (`cockpit-roadmap-inter-feature-deps`).
- **≠ socle/design d'abord** : porté par le pipeline de lancement (`cockpit-launch-pipeline`).

Ce gate ne répond qu'à **une** question : *le worker de cette feature a-t-il fini, ou reste-t-il du
travail ?* — et il y répond par la terminalité des statuts, rien d'autre.
