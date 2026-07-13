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

from cockpit.config import Settings
from cockpit.provision import _BUNDLES_DIR
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
    if all_missing:
        print(f"🔴 outillage INCOMPLET — pose les manquants (`cockpit tools install`) : "
              f"{', '.join(sorted(all_missing))}")
        return 1
    print(f"✅ outillage complet — tous les binaires déclarés résolvent sous {tools_bin(settings)}")
    return 0
