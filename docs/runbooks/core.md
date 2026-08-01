# core — runbook (primitives déterministes injectables : transport local, fs sûr, ids/slugs)

`cockpit.core` regroupe les briques PURES et sans état sur lesquelles s'appuient les couches hautes (dispatch, gate, git, projects). Invariant clé : **zéro shell implicite, zéro chemin d'hôte codé en dur, zéro littéral d'id** — tout ce qui touche l'I/O locale, le système de fichiers ou l'identité passe par ici pour rester injectable en test et borné en prod. Le seam de transport (`run`) remplace le `ssh dev@<ip>` du legacy par une exécution locale `subprocess`.

## run() — exécution locale bornée, remplace le `ssh dev@ip` legacy
`src/cockpit/core/run.py:52` · appelé par dispatch/reviewer, gate/toolchain, gate/verify, webbuild, mcp/client, daemon/app
Entrées : `argv` en **liste** (défaut, zéro shell → pas d'injection) ou `str` si `shell=True` ; kwargs `cwd`/`env`/`timeout`/`check`/`input_text`/`shell`. Garde-fous croisés en tête : `shell=True` exige une str, sinon liste (TypeError). `env`, si fourni, **remplace** l'environnement (l'appelant compose depuis `os.environ` pour hériter — usage injection ciblée `GIT_*` du writeback). Comportement : `subprocess.run` capture stdout/stderr en texte, `check=False` interne. Sorties : un `RunResult` frozen. Invariants : `check=True` lève `RunError` sur rc≠0 ; `TimeoutExpired` → `RunTimeout`.

## run_streaming() — même contrat, stdout flushé au fil de l'eau
`src/cockpit/core/run.py:98` · appelé par dispatch/worker
Comme `run` mais argv en LISTE uniquement (pas de `shell`), et écrit le stdout **ligne par ligne** dans `out_path` (open+write+flush par ligne) au lieu de ne le capturer qu'à la fin → rend le transcript d'un worker `claude -p --output-format stream-json` suivable en direct (le pont `dispatch/stream` tail ce fichier). Le stdout complet reste accumulé et rendu dans le `RunResult` final. Invariants : `timeout` honoré **même sans aucune sortie** (thread lecteur + `proc.wait(timeout)`, kill → `RunTimeout`) ; stdout ET stderr drainés en threads séparés → pas de deadlock de pipe. Sortie : `RunResult`.

## RunResult / RunError / RunTimeout — le résultat structuré et ses levées
`src/cockpit/core/run.py:39` (RunResult), `:20` (RunError), `:28` (RunTimeout)
`RunResult` (dataclass frozen) porte `argv`/`returncode`/`stdout`/`stderr` + propriété `ok` (rc==0). `RunError(RuntimeError)` : levée par `run(check=True)` sur rc≠0, encapsule le `RunResult` (attribut `.result`) et formate un message `rc=… pour <argv>: <stderr tronqué 200c>`. `RunTimeout(RuntimeError)` : levée par les deux fonctions quand le `timeout` est dépassé (process tué dans `run_streaming`).

## safe_path() — bornage anti-traversal d'un chemin sous une racine
`src/cockpit/core/fs.py:16` · appelé par terminal/pty
Port PUR de `terminal.safe_path` du legacy, **généralisé** : le legacy bornait au `/home/dev` distant en dur ; ici `root` est passée explicitement (racine par config). Entrée : `path` (str ou None) + `root` mot-clé. Normalise (`posixpath.normpath`), résout un relatif sous `root`, vide → `root`. Invariant clé : tout chemin qui sort de `root` (via `..`) → `ValueError` (fail-closed). Sortie : le chemin absolu normalisé.

## iter_jsonl() / read_jsonl() / write_jsonl() — helpers JSONL déterministes
`src/cockpit/core/fs.py:28` (iter), `:36` (read), `:44` (write)
`iter_jsonl(text)` : itère les objets d'un texte JSONL, **scinde sur `\n` uniquement** (jamais `splitlines()`, qui couperait sur U+2028/2029/85 légitimes dans une valeur JSON — leçon vault), ignore les lignes vides. `read_jsonl(file)` : lit un fichier → `list[dict]`, fichier absent → liste vide (best-effort). `write_jsonl(file, rows)` : crée les parents et écrit en JSONL **déterministe** (clés triées, `ensure_ascii=False`, `\n` final). Invariant : lecture/écriture symétriques et stables (diff-friendly).

## new_id() — uuid4 opaque, jamais un littéral
`src/cockpit/core/ids.py:27` · appelé par projects/registry, dispatch/jobs, dispatch/reviewer, dispatch/ports, projects/deployments, roadmap/model
Retourne `str(uuid.uuid4())`. Invariant : un id est toujours **généré**, jamais un littéral en dur (traçabilité + zéro collision).

## is_slug() / ensure_slug() — validation kebab-case anti-injection
`src/cockpit/core/ids.py:15` (is_slug), `:20` (ensure_slug) · appelé par projects/registry, roadmap/model
Motif `_SLUG = ^[a-z0-9]+(?:-[a-z0-9]+)*$` (miroir de `git_ops._SAFE_BRANCH_SEGMENT` du vault) : segments alphanumériques minuscules séparés par `-`, ni `..` ni `/`. `is_slug(value)` → bool (None/"" → False). `ensure_slug(value, field=…)` → retourne `value` si valide, sinon `ValueError` **fail-closed** (anti-injection : un slug finit dans un nom de branche / chemin de worktree / commande git).

## Zones non détaillées
- `__init__.py` du package — exports/agrégation, sans logique.
- Propriété `RunResult.ok` et fonctions internes `_pump_stdout`/`_pump_stderr` (threads locaux de `run_streaming`) — helpers privés couverts dans la section de leur fonction porteuse.
