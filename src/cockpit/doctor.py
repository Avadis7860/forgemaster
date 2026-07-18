"""doctor — sonde de présence de l'outillage hôte-niveau (P1 tooling-fulfillment).

Rapporte, par (type de bundle, facette), les binaires que la facette DÉCLARE dans son `settings.local.json`
(`allowedTools`) et lesquels ne résolvent PAS sur le PATH du worker (`tools_env`). C'est la MÊME vérité que
le preflight de dispatch (`tools.preflight_tools`), mais en lecture globale — une sonde d'install utilisable
en shell : `cockpit doctor; echo $?` (rc 0 = tout présent, 1 = au moins un manquant).

Déterministe (glob local des bundles vendorés + `shutil.which`), zéro réseau, zéro LLM. Rapport calqué sur
`onboarding.cli_dispatch` (lignes `✅/🔴`, `return 0 if ok else 1`).
"""
from __future__ import annotations

import argparse
import time

from cockpit.config import Settings
from cockpit.provision import _BUNDLES_DIR
from cockpit.provision import mcp as mcp_provision
from cockpit.tools import HOST_TOOLS, missing_bins, required_bins, tools_bin, tools_env


def scan(settings: Settings) -> list[dict]:
    """Pour chaque `settings.local.json` de facette des bundles vendorés, calcule les binaires requis et
    ceux qui manquent sous `tools_env`. Retourne une liste triée `{type, facet, required, missing}`. PUR
    (lecture de fichiers + `which`)."""
    env = tools_env(settings)
    rows: list[dict] = []
    for sl in sorted(_BUNDLES_DIR.glob("**/.claude/facets/*/settings.local.json")):
        parts = sl.relative_to(_BUNDLES_DIR).parts
        # …/base/.claude/…            → type "base"
        # …/types/<type>/.claude/…    → type "<type>"
        btype = "base" if parts[0] == "base" else parts[1]
        # Ne rapporte que les outils hôte-provisionnés (ceux que le worker n'installe pas lui-même).
        req = sorted(required_bins(sl) & HOST_TOOLS)
        rows.append({"type": btype, "facet": sl.parent.name, "required": req,
                     "missing": missing_bins(set(req), env)})
    return rows


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit doctor` : imprime le rapport de présence par type/facette et retourne 0 si tous les
    binaires déclarés résolvent, 1 sinon (sonde fail-loud pour l'install fraîche)."""
    rows = scan(settings)
    all_missing: set[str] = set()
    for r in rows:
        miss = r["missing"]
        icon = "🔴" if miss else "✅"
        req = ", ".join(r["required"]) or "aucun binaire"
        detail = f"MANQUE {', '.join(miss)}" if miss else "présents"
        print(f"  {icon} {r['type']}/{r['facet']} — {detail}  [{req}]")
        all_missing.update(miss)
    mcp_ok = _report_mcp(settings)
    runtime_ok = _report_runtime(settings)
    provider_ok = _report_compose_provider(settings)
    runner_ok = _report_runner(settings)
    if all_missing:
        print(f"🔴 outillage INCOMPLET — pose les manquants (`cockpit tools install`) : "
              f"{', '.join(sorted(all_missing))}")
        return 1
    if not (mcp_ok and runtime_ok and provider_ok and runner_ok):
        return 1
    print(f"✅ outillage complet — tous les binaires déclarés résolvent sous {tools_bin(settings)}")
    return 0


def _report_runtime(settings: Settings) -> bool:
    """Le moteur de run conteneur (podman/docker) est requis pour `cockpit deploy` (P2). Absent → 🔴
    **bloquant** (le deploy crasherait) avec l'action ; présent → ✅."""
    from cockpit.runtime.backend import runtime_available
    cmd = settings.compose_cmd
    if runtime_available(cmd):
        print(f"  ✅ runtime conteneur — `{cmd[0]}` présent (deploy P2 disponible)")
        return True
    print(f"  🔴 runtime conteneur — `{cmd[0]}` absent → `cockpit deploy` échouerait ; installe podman "
          "(rootless, via `provision-ct.sh`) ou configure COCKPIT_COMPOSE_CMD=docker compose")
    return False


def _report_compose_provider(settings: Settings) -> bool:
    """`cockpit deploy` lance `podman compose` (un wrapper qui DÉLÈGUE) : `podman` présent mais SANS provider
    (`podman-compose`/`docker-compose`) → `deploy up` échouerait au build (faux-vert de `_report_runtime` qui
    ne sonde que `cmd[0]`). Ne double-rougit PAS si le moteur lui-même manque (déjà rougi ci-dessus)."""
    from cockpit.runtime.backend import compose_provider_available, runtime_available
    cmd = settings.compose_cmd
    if not runtime_available(cmd):
        return True  # moteur absent → déjà signalé par `_report_runtime`, on ne redouble pas le 🔴
    if compose_provider_available(cmd):
        print(f"  ✅ provider compose — `{' '.join(cmd)}` délègue (provider présent)")
        return True
    print(f"  🔴 provider compose — `{cmd[0]}` présent mais `{' '.join(cmd)}` n'a pas de provider "
          "(`apt install podman-compose`, via `provision-ct.sh`) → `cockpit deploy` échouerait au build")
    return False


def _report_runner(settings: Settings) -> bool:
    """Le runner Playwright du gate Tier-1.5 (`cockpit gate verify`) doit être présent, sinon `verify`
    fail-close (jamais de faux-vert, mais toute vérif visuelle échoue). Défaut
    `$COCKPIT_HOME/runners/render_check.js` (semé par `provision-ct.sh`) ou override
    `COCKPIT_VERIFY_RUNNER`."""
    from cockpit.gate.verify import runner_path
    runner = runner_path(settings)
    if runner.is_file():
        print(f"  ✅ runner verify — présent ({runner})")
        return True
    print(f"  🔴 runner verify — absent ({runner}) → `cockpit gate verify` fail-close ; "
          "sème-le (`provision-ct.sh`) ou configure COCKPIT_VERIFY_RUNNER")
    return False


def _report_mcp(settings: Settings) -> bool:
    """Imprime l'état du cycle de vie du token MCP (P4) et retourne `False` si le câblage est cassé ou qu'un
    worktree porte un token expiré/expirant (le faux-négatif void-runner). Non câblé = OK honnête (install
    public sans corpus privé) → n'échoue jamais le doctor."""
    st = mcp_provision.check_lifecycle(settings, now=int(time.time()))
    if not st["configured"]:
        print(f"  ℹ️ MCP — {st['reason']}")
        return True
    if not st["healthy"]:
        print(f"  🔴 MCP — {st['reason']} → rafraîchis (`cockpit mcp wire <projet>` ou re-dispatch)")
        for s in st["stale"]:
            print(f"       ↳ {s['worktree']} (exp {s['exp']})")
        return False
    hours = (st["exp"] - int(time.time())) // 3600 if st["exp"] else 0
    print(f"  ✅ MCP câblé — token servi valide (exp dans ~{hours} h ; re-minté à chaque dispatch)")
    return True
