# forgemaster

> A **local forge that runs AI coding agents on your own machine.** It turns a project into a roadmap of
> features and tasks, dispatches one isolated worker per task — each in its own git worktree — and lets
> nothing merge that has not passed a gate.

**Status: pre-1.0.** The CLI loop is operational end to end and the schema contracts (SQLite,
`roadmap.yaml`, HTTP API) are versioned — see [`docs/schema-contract.md`](./docs/schema-contract.md).
Nothing has been released yet; [`CHANGELOG.md`](./CHANGELOG.md) is the running record.

Handing an agent a whole repository and a paragraph of intent produces work nobody asked for, in a place
nobody can review. forgemaster narrows that down to something you can inspect: **nothing is dispatched
that is not a task**, **no task runs outside its own worktree**, and **no branch merges without passing a
gate**. What a worker is able to touch follows from where it was put, not from what it was told.

It runs **on your machine**: your keys, your models, your code. There is no account, no hosted control
plane, and no telemetry — the daemon binds to `127.0.0.1` by default and phones nothing home.

Three things do cross the network, and you should know which:

1. **the agent** — every dispatch, review and interview sends your code to the agent CLI's provider. That
   is the loop, and it is by far the largest thing that leaves the host;
2. **git remotes you configure** — mirroring is opt-in and off by default;
3. **setup** — `toolchain install` and `mcp install` fetch from GitHub, npm and nodejs.org.

## What this is *not*

- **Not a SaaS, and not multi-tenant.** One daemon plus a CLI, on your host. No account, no server of ours
  in the path, no usage reporting.
- **Not a model, and not an agent.** It dispatches [Claude
  Code](https://github.com/anthropics/claude-code) (`claude -p`) as a local process and reads its
  transcript. Bring your own credentials — but the agent itself is **not swappable today**: the flags and
  the transcript parser are specific to it. forgemaster supplies the boundaries, the git plumbing and the
  gate around it.
- **Not a CI service.** The gate runs locally, before a merge, on the machine that produced the work. It
  does not replace whatever runs on your pull requests.
- **Not a remote infrastructure manager.** No ssh, no remote hosts, no fleet. Workers are local processes
  and every command goes through one local execution seam. `forgemaster deploy` does drive a **local**
  container engine (podman or docker compose) to preview a project — that is the one exception, and it is
  opt-in.
- **Not a hosted forge.** Git is **internal-first**: bare repositories on local disk. GitHub is an
  optional, swappable mirror — never a dependency of the loop.
- **Not a web app with a CLI bolted on.** The spine is the CLI plus a deterministic core; the daemon and
  the web UI are views over it, and the end-to-end loop runs headless without either.
- **Not autonomous where it matters.** The autonomous loop (`forgemaster run`) drains a feature but
  **cannot merge**: it is hard-wired to pass no human go. A merge happens only when you ask for one.

## Core model

```
project (registry) → in-repo roadmap (features → tasks DAG)
  → [gate: no task ⇒ no dispatch] → dispatch a worker (agent CLI, local process)
  → isolated git worktree (feature = branch = worktree, and that is the mutex)
  → tasks drained one at a time → gate (tests + review) → merge → worktree cleanup
```

One worktree per feature means several features advance in parallel without sharing a working tree. Under
`forgemaster run`, a feature merges when it is whole rather than task by task.

### The gate, precisely

Three tiers, and they are not equal:

| Tier | What it is | Overridable |
|---|---|---|
| **Tier-0** | deterministic: syntax, lint, types, secrets, the touched subsystem's tests | **no** — hard floor |
| **Tier-1** | code review of the diff | yes, with a traced reason |
| **Tier-1.5** | end-to-end proof that the feature actually runs | yes, with a traced reason |

The merge itself is fail-closed on an explicit go: no go, no merge, whatever the tiers say. Note that the
local HTTP API does not authenticate its callers — treat every process on the host as trusted, and do not
expose the daemon.

## Install (self-hosted)

You host your own instance. Requires **Python ≥ 3.11** and **git**. Full guide:
[`docs/install.md`](docs/install.md).

**You also need an authenticated Claude Code account on the machine** (`claude login`). Every dispatch is
refused without it — this is the loop's one hard external dependency, and its running cost.

There is no published release yet, so the wheel is built from a checkout:

```bash
# clone forgemaster next to its two vendored siblings, then:
deploy/build-wheel.sh              # needs Node ≥ 18 at build time only
pip install dist/forgemaster-*.whl # self-contained: bundles the web UI, code-map and task-map
forgemaster serve                  # http://127.0.0.1:8700 → /setup wizard on first start
```

The wheel is the simplest path: the target host needs nothing but Python. To run it as a service:
`forgemaster install-service`.

More than the loop lives behind the CLI — `forgemaster --help` is the honest inventory: an autonomous
drain (`run`), a project scaffolder driven by bundles, a web terminal, an encrypted secret store,
snapshot/restore/update, and an optional corpus MCP server.

## Companion tools

forgemaster is usable on its own. These deterministic indexes are what it hands a worker so the worker
answers from an index instead of from guesswork — each is a standalone CLI, useful without forgemaster:

- [`code-map`](https://github.com/Avadis7860/code-map) — code index (Python + TypeScript/TSX).
- [`front-map`](https://github.com/Avadis7860/front-map) — design-system index of a `web/` tree.
- [`docs-map`](https://github.com/Avadis7860/docs-map) — queryable map of `docs/` prose.
- [`task-map`](https://github.com/Avadis7860/task-map) — the mission graph: dependency DAG and anchors.
- [`forgemaster-catalogs`](https://github.com/Avadis7860/forgemaster-catalogs) — MCP server serving a typed
  corpus (third-party docs, blueprints, templates) to a worker.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pip install -e ../task-map      # imported by the roadmap resolver; the wheel vendors it, source does not
forgemaster setup               # builds the UI (Node); without it the daemon is API-only
ruff check src tests && mypy && pytest -q
```

Architecture and the deliberate boundaries: [`docs/architecture.md`](docs/architecture.md). Known debt and
the refactors that were refused: [`docs/weak-points.md`](docs/weak-points.md). Port status of each layer:
[`PORTING.md`](./PORTING.md).

The design documentation under `docs/` is written in French, and so are the web UI and the CLI messages;
the code, the identifiers and this page are in English.

## License

**AGPL-3.0-or-later** — see [`LICENSE`](./LICENSE).

This is **network software**: anyone who exposes a **modified** version to users over a network must offer
them the corresponding source (§13). forgemaster honours that clause itself — a running instance reports
the provenance of its own build on `/api/version`, so the source matching what you are served is
identifiable.

The wheel **bundles** `codemap/` and `taskmap/`, both **Apache-2.0**: their attribution lives in
[`NOTICE`](./NOTICE) and their license text in [`LICENSES/Apache-2.0.txt`](./LICENSES/Apache-2.0.txt).

A **commercial license** is available for what the AGPL does not grant — closed-source redistribution in
particular: contact@avagency.pro.
