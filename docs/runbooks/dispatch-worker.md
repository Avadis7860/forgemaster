# dispatch-worker — runbook (spawn du worker claude headless + reviewer Tier-1 : prompt, parse du résultat, findings)

La forge lance deux agents `claude -p` **headless** en local, dans le worktree de la feature, transport (`runner`) toujours injectable (les tests ne spawnent jamais un vrai `claude`). Le **worker** GÉNÈRE (allowlist code, `acceptEdits`) sur la NEXT task ; le **reviewer** GATE (read-only, `work=False`) le diff `dev...branche`. Les deux partagent le même builder d'argv et le même parseur de sortie : l'argv fixe `--output-format`, `--session-id`, l'allowlist et le DENY destructif ; le prompt part **sur stdin** (parade E2BIG), jamais dans l'argv. La sortie `stream-json` (NDJSON) est normalisée en un dict `{ok, is_error, result, …}` fail-loud, dont on tire soit le minerai décision, soit les findings du verdict.

## build_headless_argv() — argv de `claude -p` (partagé worker + reviewer)
`src/cockpit/dispatch/worker.py:107` · appelé par `dispatch_next()` et `dispatch_reviewer()`
Construit la liste d'argv PURE : `claude -p --output-format <fmt> --session-id <id>`. Si `output_format == "stream-json"` ajoute `--verbose` (exigé par `claude` pour ce format). `work=True` → `--allowedTools Bash,WebSearch,WebFetch` + `--permission-mode acceptEdits` (sans lui `claude -p` refuse Write/Edit) ; `work=False` → `--allowedTools Read,Grep,Glob` seul. Le DENY destructif (`rm`/`git push`/`git reset`/`sudo`) est posé **dans tous les cas**. `mcp_config` fourni → `--mcp-config <f>` (non-strict). Le prompt n'est JAMAIS dans l'argv. Invariant : `work` sépare génère (worker) de gate (reviewer, read-only).

## parse_headless_result() — normalise la sortie de `claude -p`
`src/cockpit/dispatch/worker.py:137` · appelé par `dispatch_next()` et via `worker.parse_headless_result` dans `dispatch_reviewer()`
Entrée `(stdout, returncode)` → dict `{ok, is_error, result, session_id, cost_usd, num_turns, error, raw}`. PUR, **fail-loud** : `rc≠0`, sortie vide, JSON illisible, `is_error`/`api_error_status` non-vide → `ok=False` + `error` peuplé (jamais de faux-vert). Délègue l'extraction de l'objet-résultat à `_result_event()` : objet JSON unique (cas `--output-format json` / runner de test) OU dernier événement `{"type":"result"}` du NDJSON `stream-json`. Tolérant à un préambule non-JSON (sentinelle de prep, bannière). Mappe `total_cost_usd`→`cost_usd`. Invariant : une sortie douteuse ne remonte jamais `ok=True`.

## dispatch_next() — spawn du worker sur la NEXT task de la feature
`src/cockpit/dispatch/worker.py:187` · appelé par `cli_dispatch` (route `cockpit dispatch <feature>`) et l'orchestrateur
Entrées : `conn`, `settings`, `feature_ref="projet/feature"`, `git`/`runner` injectables. **Gate no-task-no-dispatch** : si aucune task ou aucune task READY (`resolver.resolve_next`), retourne `{dispatched:False, reason}` sans spawn. Sinon : réserve worktree+port (`worktree.reserve`), compose le prompt (`build_worker_prompt`), mint un `session_id`, marque la task `in_progress`, journalise le job (`jobs.record_start`), injecte le MCP de corpus. Bâtit l'argv en `stream-json`/`work=True` ; le runner par défaut (`_make_default_runner`) STREAME le stdout vers `log_path` (suivi live via `dispatch/stream`). Preflight des outils + `trust_workspace` + `which claude` fail-loud AVANT spawn. Après parse : si `ok` → `write_decision_doc()` (minerai) puis commit du worktree ; sinon task revient `todo` (re-dispatchable). Garde de finalisation : toute exception échappée → `reconcile.mark_job_orphan` (jamais de job zombie) puis re-propage LOUD. Sortie : `{dispatched, reason, task?, job_id?, result?}`.

## write_decision_doc() — persiste le message final du worker en minerai local
`src/cockpit/dispatch/worker.py:140` · appelé par `dispatch_next()` dans la branche run-réussi uniquement
Écrit le `result` (message final du worker, terminé par `## Décisions prises` par mandat du prompt) **verbatim** dans `<worktree>/docs/decisions/<date_str>--<task_slug>.md`. Provenance portée par le NOM (date+slug) + l'auteur git du commit, pas de frontmatter neuf. **No-op** (retourne `None`) si `result` absent ou blanc : pas de doc vide. PUR (date injectée → testable sans horloge). Invariant : jamais de minerai orphelin sur un run raté (l'appelant ne l'invoque que si `ok`).

## build_review_prompt() — compose le prompt commission-only du reviewer
`src/cockpit/dispatch/reviewer.py:57` · appelé par `dispatch_reviewer()`
Assemble le prompt (parti sur stdin) : PERSONA.md + METHOD.md de la facette `review` committées dans le worktree (`_facet_md`), le **mandat commission-only** (un 🔴 ne porte QUE sur une ligne AJOUTÉE citée verbatim et fausse/cassée ; omission = 🟡 max ; doute → 🟡), le **cadrage** Objectif/DoD des tasks (contexte, PAS une checklist de complétude), et le **diff** `dev...branche`. Impose le contrat de sortie : un objet JSON final unique `{"base","findings":[…]}`, chaque finding avec `evidence` citant verbatim une ligne `+` du diff. PUR hors lecture des `.md` de facette.

## _extract_findings() — tire la liste findings de la sortie du reviewer
`src/cockpit/dispatch/reviewer.py:113` · appelé par `dispatch_reviewer()` sur `parsed["result"]`
Extrait `findings` du **dernier** objet JSON portant une clé `findings` (liste). Scanne les `{` en repartant du plus tardif (bloc final) vers le début ; tente aussi de couper une fence ` ```json ` de fin. Tolérant au préambule (le reviewer peut raisonner avant le JSON). Retourne `[]` si rien d'exploitable : un reviewer muet ⇒ 0 finding ⇒ verdict PASS (le fail-closed du gate est porté par la **fraîcheur** SHA, pas par ce parseur).

## _readiness() — gate déterministe « feature prête à reviewer ? »
`src/cockpit/dispatch/reviewer.py:145` · appelé par `dispatch_reviewer()` avant tout spawn
Retourne `(ok, raison)`. Prête ssi la feature a ≥1 task ET **aucune** task `todo`/`in_progress` (le worker a fini toutes ses phases). Charte : ne pas reviewer un travail inachevé → faux-positifs. Invariant : un travail en cours ne déclenche jamais la review.

## dispatch_reviewer() — spawn du review-worker Tier-1, verdict SHA-bound
`src/cockpit/dispatch/reviewer.py:186` · appelé par `cli_dispatch` (route `cockpit gate review-dispatch <feature>`), auto post-travail
Entrées : `conn`, `settings`, `feature_ref`, `git`/`runner` injectables. Enchaîne les gardes : `_readiness()` (hold si inachevé) → `feature_sha`/`diff_text` (branche absente → hold) → diff vide → hold → `review.status(...).fresh` (verdict déjà frais sur ce HEAD → skip **idempotent**) → worktree vivant. Compose le prompt (`build_review_prompt`), injecte le MCP, bâtit l'argv `work=False`/`stream-json` (read-only : ne code pas), bascule le worktree sur la facette `review` (`activate_facet` → settings.local.json read-only, sinon la facette de travail resterait active et le reviewer pourrait coder). Preflight + trust + `which claude` fail-loud, puis spawn. Parse via `worker.parse_headless_result`, journalise (`_record_job`, sans toucher au statut des tasks). Si `ok` : `_extract_findings` → `review.write_verdict(sha=head_sha, diff_text=…)` (garde `evidence⊂diff`). **Best-effort** : reviewer échoué → pas de verdict → le gate de merge bloque proprement en aval. Sortie : `{reviewed, reason, verdict?, counts?, rejected?}`.

## Zones non détaillées
- `_result_event` / `_trailing_json_object` (worker.py) : helpers PURS d'extraction de l'objet-résultat depuis un NDJSON stream-json ou un objet unique précédé d'un préambule — le cœur mécanique de `parse_headless_result`.
- `_make_default_runner` (worker.py) : usine du runner par défaut qui streame le stdout vers `log_path` (`run.run_streaming`) ; capture `out_path` sans changer le protocole `Runner`.
- `_default_runner` (reviewer.py) : runner par défaut du reviewer (`run.run`, non streamé — la review est bornée).
- `_counts` (worker.py) : rend le tally des états de tasks pour le message de refus du gate.
- `_facet_md` (reviewer.py) : lit un `.md` de facette (`PERSONA.md`/`METHOD.md`) dans le worktree, `""` si absent.
- `_record_job` (reviewer.py) : journalise le run reviewer dans `dispatch_jobs` (traçabilité + coût), `engine="reviewer-tier1"`, sans faire avancer le DAG.
- `cli_dispatch` (worker.py & reviewer.py) : routes CLI + gate d'auth Claude (jamais de spawn silencieux d'un compte hérité) ; formatage du rapport terminal.
