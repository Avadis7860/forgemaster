#!/usr/bin/env python3
"""e2e_crash_test_voidrunner.py — LE crash-test de l'épic bundle (P6), en RÉEL.

Prouve le **critère binaire de l'épic** : `void-runner` (clone OGame — le projet dont le worker avait crashé
« il ne connaît pas ses outils ») se crée **de zéro** en `browser-game`, et un **worker dispatché réel**
(`claude -p`) tourne **sans crash outils**, câblé au MCP de corpus, avec un **commit propre** — le JWT du MCP
n'atterrissant JAMAIS dans l'historique.

Étapes (via la VRAIE CLI, pas de seed) :
  1. `cockpit project create void-runner --type browser-game` → SoT semé (bundle + provenance stampée).
  2. feature + task minimale (facette `doc`, DoD trivial — le critère est « sans crash outils »).
  3. `cockpit dispatch void-runner/crash-check` → **vrai `claude -p`** ; l'injection `.mcp.json` se déclenche.
  4. Asserts : dispatch sans crash ; `.mcp.json` **injecté** + **gitignoré** ; worktree **propre** (la forge
     a tout committé) ; le `.mcp.json` **n'est dans AUCUN commit** (Bearer hors historique).

**Rejouable** : `COCKPIT_HOME` jetable, teardown en `finally`. **Hors gate natif** (vrai `claude`, lent, non
déterministe — comme `e2e_runtime.py`, règle 6 de la spec e2e). Le câblage MCP exige que le coffre résolve
le secret HMAC partagé — sinon l'injection no-op (dégradation honnête) et l'assert `.mcp.json` échoue :

Prérequis env (le MCP réellement câblé) — passer avant le lancement :
    COCKPIT_SECRET_STORE=bws
    COCKPIT_MCP_JWT_SECRET_REF=<uuid du secret MCP_JWT_SECRET dans le coffre>
    BWS_ACCESS_TOKEN=<token machine-account>   BWS_API_URL / BWS_IDENTITY_URL   (région du coffre)
Et `claude` authentifié sur la machine (le dispatch spawn un vrai worker).

Usage : .venv/bin/python scripts/e2e_crash_test_voidrunner.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV_COCKPIT = REPO / ".venv" / "bin" / "cockpit"

SLUG = "void-runner"              # le projet d'origine du crash (clone OGame browser-game)
FEATURE = "crash-check"
FACET = "doc"                     # facette la plus légère (héritée de base) — DoD trivial
ACCEPTANCE = ("Crée le fichier docs/CRASH_TEST_OK.md avec exactement une ligne : "
              "'crash-test OK — le worker tourne et connaît ses outils.'")


class Fail(Exception):
    """Un assert e2e qui casse — remonte en sortie 1 (fail-loud, jamais un faux-vert)."""


_checks: list[str] = []


def check(cond: bool, msg: str) -> None:
    mark = "✓" if cond else "✗"
    print(f"  {mark} {msg}", flush=True)
    _checks.append(mark)
    if not cond:
        raise Fail(msg)


def _run(argv: list[str], *, env: dict | None = None, timeout: float | None = None
         ) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)


def cli(env: dict, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return _run([str(VENV_COCKPIT), *args], env=env, timeout=timeout)


def git(sot: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(sot), *args])


def find_worktree(sot: Path, branch: str) -> Path | None:
    """Chemin du worktree monté sur `branch` (parse `git worktree list --porcelain`)."""
    out = git(sot, "worktree", "list", "--porcelain").stdout
    cur: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = Path(line[len("worktree "):])
        elif line.startswith("branch ") and line.endswith(f"/{branch}"):
            return cur
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true", help="ne pas supprimer le home jetable (debug)")
    a = ap.parse_args(argv)

    if not VENV_COCKPIT.exists():
        raise SystemExit(f"cockpit introuvable : {VENV_COCKPIT} (venv du repo non installé ?)")

    home = Path(tempfile.mkdtemp(prefix="cockpit-crashtest-"))
    projects_root = home / "projects"
    env = {**os.environ, "COCKPIT_HOME": str(home / "home"),
           "COCKPIT_PROJECTS_ROOT": str(projects_root)}
    sot = projects_root / SLUG / "sot.git"
    try:
        # ---- 1. création de zéro ------------------------------------------------------------------
        print("\n[1] création void-runner --type browser-game", flush=True)
        r = cli(env, "project", "create", SLUG, "--type", "browser-game")
        check(r.returncode == 0, f"projet {SLUG} créé (browser-game) [{r.returncode}] {r.stderr[:200]}")
        check(sot.exists(), f"SoT bare semé : {sot}")
        prov = git(sot, "show", "dev:.cockpit/provenance.toml").stdout
        check("browser-game" in prov, "provenance stampée browser-game (bon bundle semé dans le SoT)")
        bundle = git(sot, "show", "dev:.cockpit/bundle.toml").stdout
        check("game-design" in bundle, "bundle browser-game présent (4 facettes dont game-design)")

        # ---- 2. feature + task minimale -----------------------------------------------------------
        print("\n[2] feature + task minimale (facette doc)", flush=True)
        r = cli(env, "roadmap", "add-feature", SLUG, FEATURE, "--facet", FACET)
        check(r.returncode == 0, f"feature {SLUG}/{FEATURE} [{r.returncode}] {r.stderr[:120]}")
        r = cli(env, "task", "add", f"{SLUG}/{FEATURE}", "note", "--acceptance", ACCEPTANCE)
        check(r.returncode == 0, f"task minimale ajoutée [{r.returncode}] {r.stderr[:160]}")

        # ---- 3. dispatch RÉEL (claude -p) — le cœur du crash-test ---------------------------------
        print("\n[3] dispatch worker RÉEL (claude -p) …", flush=True)
        r = cli(env, "dispatch", f"{SLUG}/{FEATURE}", timeout=1900)
        detail = (r.stdout or r.stderr).strip()[:240]
        check(r.returncode == 0, f"dispatch sans crash outils [{r.returncode}] — {detail}")
        check("dispatch OK" in r.stdout, f"worker terminé proprement : {r.stdout.strip()[:160]}")

        # ---- 4. câblage MCP injecté + jamais committé --------------------------------------------
        print("\n[4] câblage MCP + invariant de sécurité", flush=True)
        wt = find_worktree(sot, f"feature/{FEATURE}")
        check(wt is not None, f"worktree de la feature localisé ({wt})")
        assert wt is not None
        mcp = wt / ".mcp.json"
        check(mcp.exists(), f".mcp.json injecté dans le worktree ({mcp})")
        cfg = json.loads(mcp.read_text(encoding="utf-8"))
        check("vault-catalogs" in cfg.get("mcpServers", {}),
              "le worker était câblé au MCP de corpus (serveur + Bearer)")
        check(git(wt, "check-ignore", ".mcp.json").returncode == 0,
              ".mcp.json est gitignoré (le Bearer ne PEUT PAS être committé)")
        # arbre propre post-dispatch : la forge a tout committé ; le .mcp.json (ignoré) est invisible à git.
        porcelain = git(wt, "status", "--porcelain").stdout.strip()
        check(porcelain == "", f"worktree propre après dispatch (commit propre) — reste: {porcelain[:120]!r}")
        # le .mcp.json n'apparaît dans AUCUN commit (garantie dure, indépendante du worker).
        allnames = git(sot, "log", "--all", "--name-only", "--pretty=format:").stdout
        check(".mcp.json" not in allnames, "le JWT n'est dans AUCUN commit (Bearer hors historique)")

        # info (non bloquant) : ce que le worker a produit sur la feature.
        landed = git(sot, "log", "-1", "--name-only", "--pretty=format:%s", f"feature/{FEATURE}").stdout
        print(f"  · dernier commit feature/{FEATURE} :\n    " + landed.replace("\n", "\n    "), flush=True)

        print(f"\n✅ CRASH-TEST VERT — {_checks.count('✓')} asserts", flush=True)
        return 0
    except Fail as e:
        print(f"\n❌ CRASH-TEST ÉCHEC : {e}", flush=True)
        return 1
    finally:
        if not a.keep:
            shutil.rmtree(home, ignore_errors=True)
        else:
            print(f"[--keep] home conservé : {home}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
