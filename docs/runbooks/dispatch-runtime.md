# dispatch-runtime — runbook (plomberie d'exécution : cycle de vie des jobs, stream SSE, réconcilie zombies, mutex worktree, pool de ports)

La plomberie sous l'orchestrateur de dispatch. Un **job** matérialise un worker `claude -p` sur une task (statut, port, transcript streamable) ; le **stream** pousse ses events en live ; la **réconciliation** nettoie les zombies `running` au boot ; le **worktree** est le mutex d'une feature (1 worker à la fois) et couple son cycle de vie à un **port** du pool. Cinq fichiers, cinq invariants à ne pas casser : mutex worktree, port stable+idempotent, pas de cap silencieux, tout `running` au boot = orphelin, ligne partielle jamais avalée.

## jobs.record_start() / record_finish() — ouvre / clôt un run worker (table `dispatch_jobs`)
`src/cockpit/dispatch/jobs.py:34` · `src/cockpit/dispatch/jobs.py:48` · appelés par `worker.dispatch_next`
`record_start` insère un job en statut `running` et rend son id ; `session_id` est le handle de suivi live (le chemin du transcript en dérive). `record_finish` le clôt : `done` si `parsed.ok`, sinon `failed` — mais un `status` explicite (`killed`/`cancelled`) l'emporte, et `session_id` n'est écrasé que si non nul (`COALESCE`). Renseigne les métriques du run (num_turns, cost_usd, wall_s).

## jobs.read_events() — lecture incrémentale robuste du transcript `(inode, offset)`
`src/cockpit/dispatch/jobs.py:112` · appelé par `stream.stream_job` (boucle live) et `jobs.tail`
Cœur du refactor #5 : remplace le legacy `find … | tail -F` fragile par une relecture locale par `(inode, offset)`. Reprend à `offset` ; si l'`inode` a changé (rotation), relit depuis 0 ; **s'arrête sur une ligne partielle sans consommer son offset** — elle sera relue complète au prochain appel (invariant anti-troncature). Rend `{events, offset, inode}`, les events déjà normalisés.

## jobs.tail() — drain one-shot des events d'un job
`src/cockpit/dispatch/jobs.py:136` · appelé par les lectures ponctuelles (historique / API non-live)
Résout le job, puis un unique `read_events` de tout le courant (sans offset persisté — c'est le stream qui boucle pour le live). Vide si `log_path` absent ou transcript pas encore écrit.

## jobs.transcript_path() — résout le chemin réel du transcript
`src/cockpit/dispatch/jobs.py:99` · appelé par le suivi live pour localiser le fichier écrit par le worker
Rend le chemin **déterministe** `~/.claude/projects/<encode-cwd>/<session_id>.jsonl` (encodage Claude Code : `/`, `.`, `_` → `-`) s'il existe, sinon un glob de secours en une passe, sinon `None` (worker n'a encore rien écrit). À distinguer de `dispatch_log_path` (le log streamé `<home>/logs/<session_id>.jsonl` que le daemon écrit lui-même).

## jobs.normalize_line() — une ligne JSONL → événement conversation canonique (PUR)
`src/cockpit/dispatch/jobs.py:199` · appelé par `read_events` sur chaque ligne complète
Porté verbatim de `services/aggregator/transcript_norm.py` (pur, réutilisé live ET relecture). Émet un event `assistant` (textes + tool_use résumés + usage) ou `tool_result`, `None` sinon. Ignore : lignes non-JSON, types non-conversationnels, assistant/user vides, et le **prompt user initial**. S'appuie sur les helpers `_summarize_input/_result/_usage`.

## stream.stream_job() — pont transcript → WebSocket (SSE live)
`src/cockpit/dispatch/stream.py:22` · appelé par le router WebSocket (qui a déjà `accept()` + validé le job)
Boucle la primitive pure `read_events` (offset/inode persistés entre tours) et pousse chaque event en frame JSON, puis sonde le statut DB pour clore sur terminal (`done|failed|killed`) avec une frame `{"type":"job",…}`. **Ordre lire-puis-tester = dernier drain garanti** : le worker écrit toutes ses lignes avant que `record_finish` pose le statut terminal, donc l'itération qui voit terminal a déjà relu la fin. Toute erreur transport sort proprement (`suppress`).

## reconcile.mark_job_orphan() — réconcilie UN job zombie (`running` → `killed`)
`src/cockpit/dispatch/reconcile.py:30` · appelé par la garde de finalisation de `worker.dispatch_next` et par `reconcile_orphans`
Passe le job `running` → **`killed`** (statut sanctionné, distinct de `failed` réservé à un run qui a *rapporté* un échec) ET sa task `in_progress` → `todo` (le vrai débloqueur : le résolveur re-voit la task READY). Le `WHERE status='running'` **scope chaque écriture** : un job déjà abouti n'est jamais clobbéré (rend `False`). **Worktree et port NE sont PAS relâchés** : réservations idempotentes réutilisées au re-dispatch.

## reconcile.reconcile_orphans() — nettoie TOUS les zombies au boot
`src/cockpit/dispatch/reconcile.py:47` · appelé par le lifespan de `daemon/app`
Le dispatch est **synchrone in-process** (le worker tourne dans le threadpool de la requête) → aucun thread de dispatch ne survit un restart, donc **tout `running` observé au boot est orphelin par construction** — réconcilié sans heartbeat ni sonde PID (déterministe). Boucle `mark_job_orphan` sur chaque `running`, rend les ids réellement réconciliés. Idempotent : un 2ᵉ appel rend `[]`.

## worktree.reserve() / release() — réserve / démonte le worktree (mutex) + son port couplé
`src/cockpit/dispatch/worktree.py:36` · `src/cockpit/dispatch/worktree.py:57` · appelés au dispatch / au merge ET au reset
**Un worktree = le mutex d'une feature** (1 worker à la fois ; N features ⇒ N worktrees parallèles). `reserve` (idempotent) crée le worktree attaché au SoT partagé sur `feature/<slug>` ancré sur `dev` (flock dans `git/internal`), active la facette (`settings.local.json` gitignoré) et couple un port stable via `ports.reserve`. `release` = teardown dans l'**ordre spec** : `remove_worktree` **PUIS** `ports.release` — et ne supprime **jamais** la branche (c'est à `gate.merge` de faire `delete_branch` après). Idempotent des deux côtés.

## worktree.audit() — détecte les orphelins port↔worktree (doit rester à 0)
`src/cockpit/dispatch/worktree.py:75` · appelé par la santé/diagnostic du dispatch
Vérifie l'invariant de couplage dans les deux sens : port réservé sans worktree sur disque (`port-sans-worktree`), et worktree sur disque sans réservation de port (`worktree-sans-port`). Rend la liste d'anomalies — doit être vide après tout merge/reset propre.

## worktree.worktree_path_for() — chemin déterministe d'un worktree
`src/cockpit/dispatch/worktree.py:27` · appelé par `reserve`, `release`, `audit`
`<projects_root>/<project>/worktrees/<feature>`. Pur, déterministe — pas d'I/O.

## ports.free_port() — 1er port libre de la plage (déterministe, PAS de cap silencieux)
`src/cockpit/dispatch/ports.py:48` · appelé par `ports.reserve`
Parcours **croissant déterministe** de `DEFAULT_RANGE` (5170–5249) : écarte les ports déjà au registre et ceux vus pris par la sonde best-effort injectée (sonde injoignable → on fait confiance au registre). **Saturation → lève `PortPoolExhausted`** (`ports.py:29`), jamais un retour silencieux ou un port réutilisé de force : le cap est bruyant par contrat.

## ports.reserve() / release() — réservation stable idempotente pour `(project, purpose)`
`src/cockpit/dispatch/ports.py:67` · `src/cockpit/dispatch/ports.py:91` · appelés par `worktree.reserve`/`release`
`reserve` rend le **même port** au re-provision d'un worktree (idempotent sur `(project, purpose)`) ; robuste à une course concurrente (deux dispatchs simultanés) via retry sur collision d'unicité (`UNIQUE(port)`, jusqu'à `_MAX_RACE_RETRY=5`, puis `PortPoolExhausted`). `release` supprime la réservation et rend celle fermée, ou `None` si absente — un double release ne lève pas (idempotent).

## Zones non détaillées
- **jobs.py** — `get_job`/`list_jobs` (accès DB : un job / les jobs d'une feature triés récent→ancien, servent la découverte du job à streamer) ; `dispatch_log_path`/`expected_transcript_path` (résolution de chemins, l'`expected` en amont de `transcript_path`) ; `_truncate`/`_summarize_input`/`_summarize_result`/`_usage` (helpers purs du normaliseur, appelés par `normalize_line`).
- **worktree.py** — `_purpose` (clé `worktree:<feature>`), `_iter_worktree_dirs` (scan disque pour `audit`).
- **ports.py** — `local_probe` (sonde `connect_ex` par défaut, mockée en test), `list_reservations` (réservations actives triées, consommée par `worktree.audit`), `_reserved_ports`/`_now` (helpers).
