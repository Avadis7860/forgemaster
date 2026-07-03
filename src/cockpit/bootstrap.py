"""bootstrap — amorçage de l'**édition maintainer** : adopte les OUTILS du framework déclarés dans un
manifeste (`bootstrap.yaml` sous COCKPIT_HOME), au 1er démarrage, via le primitif d'adoption (P1
`registry.create_project(source_url=…)` → `clone_sot`). Ils apparaissent alors en **section « Outils »**
du rail (`kind=tool`), avec leur VRAI contenu git.

Invariants :
- **Donnée, pas secret** : le manifeste ne porte que des `credential_ref` OPAQUES (repo privé) ou rien
  (repo public). Le token se lie au wizard (`/setup`) ou via `cockpit bootstrap --token-file` → store ;
  il n'existe en clair que le temps d'un `put`.
- **Idempotent** : un slug déjà présent → `skipped` (jamais un doublon, ré-exécution sûre).
- **Fail-loud vs best-effort** : un manifeste structurellement invalide → `ValueError` (abort, jamais un
  demi-amorçage) ; une erreur OPÉRATIONNELLE sur une entrée (clone injoignable…) → `failed`, la boucle
  continue pour les autres.
- **Absent → no-op** : pas de manifeste = install générique (le wizard reste intact).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from cockpit.config import Settings
from cockpit.db import store as db_store
from cockpit.projects import registry
from cockpit.secrets import build_store, cred_resolver

MANIFEST_NAME = "bootstrap.yaml"

_TEMPLATE = """\
# bootstrap.yaml — édition maintainer : outils du framework adoptés au 1er démarrage.
# AUCUN secret ici. `credential_ref` = référence opaque vers le coffre (repo PRIVÉ) ; absente = clone
# anonyme (repo PUBLIC). Le token de lecture se lie au wizard (/setup) ou par
# `cockpit bootstrap --token-file <fichier>` — jamais dans ce fichier ni dans un repo.
tools:
  - slug: cockpit
    source_url: https://github.com/OWNER/cockpit.git
  # - slug: code-map
  #   source_url: https://github.com/OWNER/code-map.git
  #   credential_ref: <réf-store>     # repo privé : réf posée par --token-file / le wizard
"""


def manifest_path(settings: Settings) -> Path:
    """Chemin du manifeste d'outils : `<COCKPIT_HOME>/bootstrap.yaml`. Déterministe, sous config."""
    return settings.home / MANIFEST_NAME


def load_manifest(settings: Settings) -> list[dict[str, Any]] | None:
    """Charge et VALIDE le manifeste. `None` si absent (→ no-op propre). `ValueError` (abort loud) si
    présent mais structurellement invalide — jamais un demi-amorçage. Retourne une liste normalisée
    d'entrées `{slug, source_url, kind, credential_ref}`."""
    path = manifest_path(settings)
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"manifeste illisible ({path}) : {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("tools"), list):
        raise ValueError(f"manifeste invalide ({path}) : clé `tools` (liste) attendue")
    entries: list[dict[str, Any]] = []
    for i, tool in enumerate(raw["tools"]):
        if not isinstance(tool, dict):
            raise ValueError(f"manifeste invalide ({path}) : entrée #{i} n'est pas un mapping")
        slug, source_url = tool.get("slug"), tool.get("source_url")
        if not isinstance(slug, str) or not slug:
            raise ValueError(f"manifeste invalide ({path}) : entrée #{i} sans `slug`")
        if not isinstance(source_url, str) or not source_url:
            raise ValueError(f"manifeste invalide ({path}) : outil {slug!r} sans `source_url`")
        cref = tool.get("credential_ref")
        if cref is not None and not isinstance(cref, str):
            raise ValueError(f"manifeste invalide ({path}) : outil {slug!r} `credential_ref` non-string")
        entries.append({"slug": slug, "source_url": source_url,
                        "kind": tool.get("kind", "tool"), "credential_ref": cref})
    return entries


def run_bootstrap(conn: sqlite3.Connection, settings: Settings, *,
                  manifest: list[dict[str, Any]], shared_ref: str | None = None) -> dict:
    """Adopte chaque outil du manifeste — IDEMPOTENT (slug déjà présent → `skipped`). Credential résolu
    **par entrée** : `credential_ref` de l'entrée (un token par repo, D6), sinon le `shared_ref` fourni
    (token de lecture partagé du wizard/`--token-file`), sinon anonyme (repo public, D7). Câble
    `mirror_remote=source_url` (miroir gratuit, réactivable en écriture plus tard). Une erreur
    opérationnelle sur une entrée → `failed`, la boucle continue. Retourne
    `{created:[slug], skipped:[slug], failed:[{slug, error}]}`."""
    report: dict[str, list[Any]] = {"created": [], "skipped": [], "failed": []}
    existing = {p["slug"] for p in registry.list_projects(conn)}
    resolve = cred_resolver(settings)
    for entry in manifest:
        slug = entry["slug"]
        if slug in existing:                             # idempotence : ne jamais re-cloner un slug présent
            report["skipped"].append(slug)
            continue
        ref = entry.get("credential_ref") or shared_ref
        try:
            registry.create_project(
                conn, settings, slug=slug, kind=entry.get("kind", "tool"),
                source_url=entry["source_url"], mirror_remote=entry["source_url"],
                credential_ref=ref, cred_resolver=resolve)
            report["created"].append(slug)
            existing.add(slug)
        except (ValueError, KeyError) as exc:        # clone injoignable / course : échec ISOLÉ, on poursuit
            report["failed"].append({"slug": slug, "error": str(exc)})
    return report


def preview(conn: sqlite3.Connection, settings: Settings) -> dict:
    """Aperçu IDEMPOTENT (GET) : ce que l'amorçage FERAIT, sans aucun effet. `available` = manifeste
    présent & valide ; par outil, `adopted` = slug déjà présent. Aucun secret. Manifeste absent →
    `available:false` (le wizard reste une install générique). Manifeste invalide → `ValueError` (400)."""
    manifest = load_manifest(settings)
    if manifest is None:
        return {"available": False, "tools": [], "adopted": 0, "total": 0}
    existing = {p["slug"] for p in registry.list_projects(conn)}
    tools = [{"slug": e["slug"], "source_url": e["source_url"], "kind": e["kind"],
              "adopted": e["slug"] in existing} for e in manifest]
    return {"available": True, "tools": tools,
            "adopted": sum(1 for t in tools if t["adopted"]), "total": len(tools)}


def write_template(settings: Settings) -> Path:
    """Écrit un manifeste gabarit commenté sous COCKPIT_HOME (`cockpit bootstrap --init`). Ne JAMAIS
    écraser un manifeste existant (même garde no-overwrite que `service.py`). Retourne le chemin."""
    settings.home.mkdir(parents=True, exist_ok=True)
    path = manifest_path(settings)
    if path.exists():
        raise ValueError(f"manifeste déjà présent : {path} (ne pas écraser)")
    path.write_text(_TEMPLATE, encoding="utf-8")
    return path


def _resolve_shared_ref(settings: Settings, token_file: str | None) -> str | None:
    """Voie `--token-file` : lit un token de lecture partagé (jamais en argv), le range dans le store,
    retourne la réf opaque. Le token n'existe en clair que le temps du `put`."""
    if not token_file:
        return None
    token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    return build_store(settings).put(token, label="bootstrap-read")


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit bootstrap` : `--init` écrit un gabarit ; sinon lit le manifeste et adopte chaque
    outil (idempotent). `--token-file <f>` lie un token de lecture partagé (voie fichier). Manifeste
    absent = no-op propre. Code de sortie 1 s'il reste des échecs (fail-loud)."""
    if getattr(args, "init", False):
        try:
            path = write_template(settings)
        except ValueError as exc:
            print(f"erreur : {exc}")
            return 1
        print(f"manifeste gabarit écrit → {path} (édite-le puis relance `cockpit bootstrap`)")
        return 0
    try:
        manifest = load_manifest(settings)
        shared_ref = _resolve_shared_ref(settings, getattr(args, "token_file", None))
    except (ValueError, OSError) as exc:
        print(f"erreur : {exc}")
        return 1
    if manifest is None:
        print(f"aucun manifeste ({manifest_path(settings)}) — rien à amorcer "
              "(`cockpit bootstrap --init` pour un gabarit).")
        return 0
    conn = db_store.open_db(settings)
    try:
        report = run_bootstrap(conn, settings, manifest=manifest, shared_ref=shared_ref)
    finally:
        conn.close()
    for slug in report["created"]:
        print(f"  ✅ adopté : {slug}")
    for slug in report["skipped"]:
        print(f"  ⏭  déjà présent : {slug}")
    for fail in report["failed"]:
        print(f"  🔴 échec : {fail['slug']} — {fail['error']}")
    print(f"amorçage : {len(report['created'])} adopté(s), {len(report['skipped'])} ignoré(s), "
          f"{len(report['failed'])} échec(s)")
    return 0 if not report["failed"] else 1
