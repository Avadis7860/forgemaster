---
name: first-session-interview
description: Mener l'interview de conception (1ʳᵉ session) d'un browser-game neuf — d'ABORD proposer un STYLE servi (patron verrouillé + templates de domaine, énuméré via le MCP de corpus) puis cadrer concept, boucle de jeu et économie avec l'humain (au terminal, NO-CODE), les fixer dans docs/design.md, et dériver la roadmap de lancement jouable via roadmap-decompose. Variante game-design du first-session-interview de base.
inputs: [browser-game neuf semé (socle-design nu), humain présent au terminal]
outputs: [docs/design.md renseigné (concept + boucle + économie), roadmap de lancement jouable authorée, socle prêt à drainer]
related_catalogs: []
---

# first-session-interview (browser-game) — d'un socle de design nu à une roadmap jouable

## Quand l'utiliser

À la **première session** d'un browser-game neuf, lancé par `cockpit interview <projet>`. Le projet est semé
avec un socle `socle-design` **nu** (`docs/design.md` en « à renseigner »). Tu mènes la conception en
**NO-CODE** (facette game-design) : ta production est de la **décision écrite**, pas du code.

Tu tournes en **INTERACTIF** (un humain est en face). **Interviewe-le** — par lots de questions — jusqu'à ce
que le jeu tienne debout sur le papier. Suis aussi la `METHOD.md` de ta facette game-design (décider, écrire
dans `docs/design.md`, ancrer l'implémentabilité, ne toucher ni code ni gate).

## Le résultat visé (critère binaire)

1. `docs/design.md` ne porte **plus aucun « (à renseigner) »** dans ses sections de conception :
   - **Concept & périmètre jouable** : le pitch en une phrase + la définition binaire de « jouable » du
     premier jalon — « jouable » = **un état change de façon observable APRÈS un geste du joueur** (clic →
     tick → nouvel écran), pas un écran statique. La preuve Tier-1.5 l'exigera (marqueur post-interaction).
   - **Boucle de jeu** : la décision répétée du joueur (ce qu'il fait en boucle, et pourquoi c'est
     intéressant).
   - **Économie & équilibrage** : les ressources / coûts / taux **chiffrés** (des nombres justifiés, pas
     « à équilibrer plus tard »).
2. La roadmap porte les **features d'amorçage jouable** — typiquement scaffold serveur (backend), schémas
   partagés (backend), premier jalon jouable (frontend) — chacune facette + tasks (`depends_on` +
   `acceptance`), et `cockpit roadmap check <projet>` est **vert**.

## Protocole

### 0. Proposer un STYLE servi (capital-token via MCP) — AVANT de concevoir
Un browser-game peut partir d'un **style** de conception déjà distillé (patron verrouillé + templates de
domaine), servi par le **MCP de corpus**. Ton worktree a un accès MCP natif (`/mcp`) **si** le corpus est
câblé — utilise-le en solve-mode :
- **Énumère les styles** : `list_collections("blueprint")` → garde les entrées dont le champ `class` commence
  par **`browser-game:`** (ex. `browser-game:ogame-rogue-like-pve`). Chaque entrée = `{class, id, title}`.
- **Propose-les à l'humain** (id + titre) comme points de départ, **plus** l'option « aucun / sur-mesure ».
- **Sur choix d'un style** : `read("blueprint", "<id>")` → tire la **guidance** (design verrouillé, boucle,
  formules, pointeurs de templates de domaine). Tu **cadres** tout le reste de l'interview sur cette guidance,
  et le jeu se **construit** ensuite d'après elle — tu ne re-débats pas ce qui y est déjà tranché.
- **Dégradation honnête** : MCP non câblé (`list_collections` vide/erreur), aucun style `browser-game:`, ou
  l'humain veut du sur-mesure → poursuis en **générique** (étapes 1→4 telles quelles). Jamais de blocage.

### 1. Interviewer pour concevoir (concept → boucle → économie)
Si un style a été choisi à l'étape 0, **cadre chaque question sur SA guidance** (ne réinvente pas ce qui y est
verrouillé). Sinon, interroge l'humain jusqu'à pouvoir écrire, sans inventer :
- **Concept** : à quoi on joue, la fantaisie procurée, ce qui rend le premier jalon « jouable » (binaire).
- **Boucle de jeu** : la décision que le joueur répète (récolter / arbitrer / risquer…), la tension qui la
  rend intéressante.
- **Économie** : ressources, coûts, gains, taux — **chiffrés**. Vérifie que chaque règle est
  **implémentable** (lecture seule du modèle serveur via `codemap where`, sans écrire de code).

### 2. Fixer la conception dans `docs/design.md`
Écris chaque décision dans **`docs/design.md`** (une section = une décision vérifiable, ses nombres
justifiés). Remplace tout « (à renseigner) ». Après avoir touché `docs/`, `docsmap build`.

### 3. Dériver la roadmap de lancement jouable (skill roadmap-decompose)
Applique **`roadmap-decompose`** pour décomposer le design en features d'amorçage jouable et les AUTHORER :

```bash
cockpit roadmap add-feature <projet> scaffold-serveur --facet backend --title "Scaffold serveur"
cockpit roadmap add-feature <projet> schemas-partages  --facet backend --title "Schémas partagés"
cockpit roadmap add-feature <projet> jalon-jouable     --facet frontend --title "Premier jalon jouable"
cockpit task add <projet>/<feature> <task> --acceptance "Critère binaire, testé : …"
cockpit roadmap check <projet>          # VERT (0 issue)
```

Séquence back → merge → front (le front consomme le contrat serveur mergé — cf. `roadmap-decompose`). Chaque
task porte une **`acceptance` binaire** (jouable = observable **après un geste**, pas « faire marcher » ni un
écran statique — l'`acceptance` du jalon jouable doit nommer l'état qui change après l'interaction).

### 4. Rendre la main
Préviens l'humain. La forge **vérifie** (roadmap check vert + ≥1 feature de travail) et clôt le socle-design
en `done` — ne marque pas les tasks toi-même. `cockpit run <projet>` draine ensuite les features jouables.

## Anti-patterns

- **Ignorer les styles servis** — si le MCP expose des styles `browser-game:`, propose-les AVANT de concevoir à blanc ; réinventer un design déjà distillé gaspille le capital.
- **Concevoir sans l'humain** — l'interview EST le point : ne devine pas le jeu.
- **Économie « à équilibrer plus tard »** — pas de nombres = pas de design vérifiable ; chiffre maintenant.
- **Écrire du code** — tu es NO-CODE ; ta sortie est de la conception que `backend`/`frontend` implémentent.
- **Roadmap non vérifiée** — ne rends pas la main tant que `roadmap check` n'est pas vert.
