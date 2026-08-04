"""provision.manage — surface de **gestion** des bundles (CLI `forgemaster bundle …`). Façade MINCE de
présentation sur l'API `provision` (`discover_types`, `validate_bundle`, `read_bundle_manifest`,
`load_bundle`) : elle **appelle et présente**, elle ne ré-implémente aucune logique de découverte, de
validation ni de composition. Garder `provision/__init__.py` pur (bibliothèque sans I/O de présentation) ;
tout le formatage/dispatch opérateur vit ici.

Sa seule valeur ajoutée sur `list_valid_types` (qui écarte les bundles cassés en silence, fail-closed
correct pour l'*offre*) : **surfacer** un bundle cassé avec sa raison — le registre devient
*diagnostiquable*, pas seulement *filtré*. Contrat de couche standard `cli_dispatch(settings, args) -> int`
(comme `roadmap.model`, `toolsync`…). `settings` est inutilisé : le registre est le filesystem vendoré,
indépendant de `--home`/`--projects-root`."""
from __future__ import annotations

import argparse

from forgemaster.config import Settings
from forgemaster.provision import (
    BundleError,
    check_launch_roadmap_drift,
    discover_types,
    load_bundle,
    read_bundle_manifest,
    validate_bundle,
)


def _list(args: argparse.Namespace) -> int:
    """Vue d'ensemble du registre : une ligne par type découvert, valides ET cassés (✓ + facettes /
    ✗ + raison). Non-fatal (retour 0) — c'est `validate` qui porte le code de sortie ; ici on *montre*."""
    types = discover_types()
    tw = max((len(t) for t in types), default=4)
    for t in types:
        try:
            validate_bundle(t)
            manifest = read_bundle_manifest(t)
        except BundleError as exc:
            print(f"{t:<{tw}}  {'--':<4}  ✗  {exc}")
            continue
        ver = f"v{manifest.get('version', '')}"
        facets = ", ".join(manifest.get("facets", []))
        print(f"{t:<{tw}}  {ver:<4}  ✓  {facets}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    """Diagnostic actionnable : valide un type (si fourni) ou tous. `✓ <type>` / `✗ <type> : <raison>`
    + synthèse. Valide la **structure** du bundle (fail-closed `validate_bundle`) ET la **cohérence
    SoT-and-derive** de la graine (`check_launch_roadmap_drift` : pas de drift vs template central vendoré).
    **Retour 1 dès qu'un bundle est invalide OU a dérivé** (utilisable en gate/CI)."""
    targets = [args.type] if args.type else list(discover_types())
    invalid = 0
    for t in targets:
        try:
            validate_bundle(t)
        except BundleError as exc:
            print(f"✗ {t} : {exc}")
            invalid += 1
            continue
        drift = check_launch_roadmap_drift(t)
        if drift:
            print(f"✗ {t} : {'; '.join(drift)}")
            invalid += 1
        else:
            print(f"✓ {t}")
    print(f"{len(targets) - invalid} valides, {invalid} invalides")
    return 1 if invalid else 0


def _show(args: argparse.Namespace) -> int:
    """Détail d'UN bundle : version, project_type, facettes, default_facet, nb de fichiers composés,
    validité. Type hors registre / cassé → message lisible + retour 1 (jamais de traceback)."""
    t = args.type
    try:
        validate_bundle(t)
        manifest = read_bundle_manifest(t)
        n_files = len(load_bundle(t))
    except BundleError as exc:
        print(f"✗ {t} : {exc}")
        return 1
    rows = (
        ("bundle", t),
        ("version", str(manifest.get("version", ""))),
        ("project_type", str(manifest.get("project_type", ""))),
        ("facettes", ", ".join(manifest.get("facets", []))),
        ("default", str(manifest.get("default_facet", ""))),
        ("fichiers", str(n_files)),
        ("valide", "✓"),
    )
    for label, value in rows:
        print(f"{label:<14}{value}")
    return 0


def _version(args: argparse.Namespace) -> int:
    """Version brute, scriptable : un type → la **string nue** ; sans arg → une ligne `<type>  <version>`
    par type découvert (`--` si le manifeste est illisible)."""
    if args.type:
        try:
            print(read_bundle_manifest(args.type).get("version", ""))
        except BundleError as exc:
            print(f"✗ {args.type} : {exc}")
            return 1
        return 0
    types = discover_types()
    tw = max((len(t) for t in types), default=4)
    for t in types:
        try:
            ver = str(read_bundle_manifest(t).get("version", ""))
        except BundleError:
            ver = "--"
        print(f"{t:<{tw}}  {ver}")
    return 0


def _derive(args: argparse.Namespace) -> int:
    """Régénère (ou vérifie, `--check`) le seed **dérivé** d'un type depuis son template corpus vendoré
    (`provision.derive`, build-time). Sans `--type` : tous les dérivables. `--check` n'écrit rien et
    **retourne 1 dès qu'un overlay a dérivé** de son template (gate/CI). Import paresseux : `bundle list`
    et consorts n'ont pas à charger le générateur."""
    from forgemaster.provision import derive
    targets = [args.type] if getattr(args, "type", None) else list(derive.derivable_types())
    if not targets:
        print("aucun type dérivable (aucun derive/<type>/)")
        return 0
    rc = 0
    for t in targets:
        try:
            if args.check:
                drift = derive.check_drift(t)
                if drift:
                    print(f"✗ {t} : overlay dérivé du template — {', '.join(drift)}")
                    rc = 1
                else:
                    print(f"✓ {t} : en phase avec le template")
            else:
                written = derive.apply_derivation(t)
                print(f"✓ {t} : {len(written)} chemin(s) régénéré(s) — {', '.join(written)}")
        except derive.DeriveError as exc:
            print(f"✗ {t} : {exc}")
            rc = 1
    return rc


_ACTIONS = {"list": _list, "validate": _validate, "show": _show, "version": _version, "derive": _derive}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `args.action` vers le sous-handler. `settings` inutilisé (registre = filesystem vendoré) —
    signature gardée pour l'uniformité du contrat de couche."""
    return _ACTIONS[args.action](args)
