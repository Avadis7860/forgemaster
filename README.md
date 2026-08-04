# forgemaster

> A **local forge that runs AI coding agents on your own machine.** It turns a project into a roadmap of
> features and tasks, dispatches one isolated worker per task — each in its own git worktree — and lets
> nothing merge that has not passed a gate.

**Status: pre-1.0.** The CLI loop is operational end to end; the schema contracts (SQLite, `roadmap.yaml`,
HTTP API) are frozen and versioned — see [`docs/schema-contract.md`](./docs/schema-contract.md) and
[`CHANGELOG.md`](./CHANGELOG.md).

Handing an agent a whole repository and a paragraph of intent produces work nobody asked for, in a place
nobody can review. forgemaster narrows that down to something you can inspect: **nothing is dispatched
that is not a task**, **no task runs outside its own worktree**, and **no branch merges without passing a
gate**. What a worker is able to touch follows from where it was put, not from what it was told.

It runs **on your machine**: your keys, your models, your code. There is no hosted control plane and no
telemetry. Nothing leaves the host unless you configure a remote yourself.

## What this is *not*

- **Not a SaaS, and not multi-tenant.** It is a single daemon bound to localhost plus a CLI. There is no
  account, no server of ours in the path, and no usage reporting.
- **Not a model, and not an agent.** It dispatches an agent CLI (`claude -p`) as a local process and reads
  its transcript. Bring your own agent and your own credentials; forgemaster supplies the boundaries, the
  git plumbing and the gate around it.
- **Not a CI service.** The gate runs locally, before a merge, on the machine that produced the work. It
  does not replace whatever runs on your pull requests.
- **Not an infrastructure manager.** No Proxmox, no containers, no ssh, no remote hosts. Workers are local
  processes; every command goes through one local execution seam.
- **Not a hosted forge.** Git is **internal-first**: bare repositories on local disk, zero network. GitHub
  is an optional, swappable mirror — not a dependency of the loop.
- **Not a web app with a CLI bolted on.** The spine is the CLI plus a deterministic core; the daemon and
  the web UI are views over it, and the end-to-end loop runs headless without either.
- **Not autonomous.** A merge and a destroy are never taken by a worker on its own — they are fail-closed
  on an explicit human go.

## Core model

```
project (registry) → in-repo roadmap (features → tasks DAG)
  → [gate: no task ⇒ no dispatch] → dispatch a worker (agent CLI, local process)
  → isolated git worktree (feature = branch = worktree, and that is the mutex)
  → sequential tasks within a feature → gate (tests + review) → merge → worktree cleanup
```

One worktree per feature means several features advance in parallel without sharing a working tree. A
feature merges when it is whole, not task by task.

## Install (self-hosted)

You host your own instance. Full guide: [`docs/install.md`](docs/install.md).

```bash
# simplest path: the packaged wheel bundles the web UI and code-map (no Node required)
pip install forgemaster-0.1.0-py3-none-any.whl
forgemaster serve                 # http://127.0.0.1:8700 → /setup wizard on first start
```

From source: `pip install -e .` then `forgemaster setup` (builds the UI and wires code-map from a sibling
`../code-map` clone; Node required). To run it as a service: `forgemaster install-service`.

## Companion tools

forgemaster is usable on its own. These deterministic indexes are what it hands a worker so the worker
answers from an index instead of from guesswork — each is a standalone CLI, useful without forgemaster:

- [`code-map`](https://github.com/Avadis7860/code-map) — code index (Python + TypeScript/TSX).
- [`front-map`](https://github.com/Avadis7860/front-map) — design-system index of a `web/` tree.
- [`docs-map`](https://github.com/Avadis7860/docs-map) — queryable map of `docs/` prose.
- [`task-map`](https://github.com/Avadis7860/task-map) — mission graph: readiness ranking and anchors.
- [`forgemaster-catalogs`](https://github.com/Avadis7860/forgemaster-catalogs) — MCP server serving a typed
  corpus (third-party docs, blueprints, templates) to a worker.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
forgemaster setup           # builds the UI (Node); without it the daemon is API-only
forgemaster --help
ruff check src tests && mypy && pytest -q
```

Architecture and the deliberate boundaries: [`docs/architecture.md`](docs/architecture.md). Known debt and
the refactors that were refused: [`docs/weak-points.md`](docs/weak-points.md). Port status of each layer:
[`PORTING.md`](./PORTING.md).

## License

**AGPL-3.0-or-later** — see [`LICENSE`](./LICENSE).

This is **network software**: anyone who exposes a **modified** version to users over a network must offer
them the corresponding source (§13).

The wheel **bundles** `codemap/` and `taskmap/`, both **Apache-2.0**: their attribution lives in
[`NOTICE`](./NOTICE) and their license text in [`LICENSES/Apache-2.0.txt`](./LICENSES/Apache-2.0.txt).

A **commercial license** is available for what the AGPL does not grant — closed-source redistribution in
particular: contact@avagency.pro.
