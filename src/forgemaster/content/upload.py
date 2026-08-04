"""content.upload — écrit un asset uploadé par l'opérateur dans le worktree d'un projet, sous
`docs/design/<slug>/<filename>`. Symétrique de `design.seed.write_design_seed` : **PUR filesystem**,
fail-soft, **no-op sur data vide** (retourne `None`, rien écrit). Aucune horloge, aucun réseau.

Applique les **bornes verrouillées** de la spec (`docs/specs/project-content-upload.md`, règles #5-#7) avant
d'écrire un octet — dans cet ordre, car un secret peut porter une extension autorisée (`credentials.md`) :

1. **exclusion des secrets** (règle #6) — le canal n'est pas le BWS ;
2. **allow-list de type** (règle #5) — images + texte/doc, rien d'autre ;
3. **cap de taille** (règle #5) — **lève avec pointeur**, jamais de troncature ;
4. **garde path-traversal** (règle #7) — `filename`/`dest_slug` confinés sous `docs/design/`.

Les rejets lèvent des sous-classes distinctes de `UploadRejected` pour que la vue HTTP (Phase 2) mappe
413 (taille) / 415 (type) / 400 (secret, traversal, nom) sans réinspecter le message.
"""
from __future__ import annotations

from pathlib import Path

# Cap de taille (règle #5) : généreux pour une charte/schéma/PDF de référence, borné pour rester un canal de
# CONTENU (pas un transfert de gros binaires). Dépassement → `UploadTooLarge` (jamais de troncature).
_UPLOAD_MAX_BYTES = 10 * 1024 * 1024

# Allow-list d'extensions (règle #5) : images servables + texte/doc de référence. Fermée (reste rejeté).
_ALLOWED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".svg", ".webp",   # images (charte, schéma, référence)
    ".md", ".txt", ".css", ".pdf",              # texte / doc / tokens
})

# Racine de confinement (règle #7 + destination règle #2) : tout atterrit sous `<worktree>/docs/design/`.
_DEST_ROOT = ("docs", "design")


class UploadRejected(ValueError):
    """Rejet d'un upload par une borne verrouillée (secret, nom/traversal invalide). → 400 côté HTTP.
    Deux sous-classes portent un code plus précis (taille → 413, type → 415)."""


class UploadTooLarge(UploadRejected):
    """La taille dépasse `_UPLOAD_MAX_BYTES` (règle #5). → 413. Jamais de troncature : on rejette."""


class UploadTypeRejected(UploadRejected):
    """L'extension est hors de l'allow-list (règle #5). → 415."""


def _reject_if_secret(name: str) -> None:
    """Rejet explicite (règle #6) d'un nom de secret : les secrets passent par le BWS, **jamais** ce canal.
    Vérifié AVANT le type car un secret peut porter une extension autorisée (`credentials.md`, `.env.md`)."""
    low = name.lower()
    if (low == ".env" or low.startswith(".env.")
            or low.startswith("id_rsa")
            or low.startswith("credentials")
            or low.endswith((".pem", ".key", ".p12", ".pfx"))):
        raise UploadRejected(
            f"nom de secret rejeté : {name!r}. Les secrets passent par le BWS secret manager, "
            f"jamais par le canal d'upload de contenu.")


def _validate_filename(filename: str) -> str:
    """Valide `filename` comme **nom nu** confiné (règle #7) : ni vide, ni séparateur, ni référence parente,
    ni chemin absolu. Puis rejette les secrets (#6) et les types hors allow-list (#5). Retourne le nom sûr."""
    name = filename.strip()
    if not name:
        raise UploadRejected("nom de fichier vide")
    if name in (".", "..") or "/" in name or "\\" in name or name != Path(name).name:
        raise UploadRejected(f"nom de fichier invalide (path-traversal) : {filename!r}")
    _reject_if_secret(name)
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise UploadTypeRejected(
            f"type non autorisé : {ext or '(sans extension)'}. Autorisés : {sorted(_ALLOWED_EXTENSIONS)}.")
    return name


def _validate_dest_slug(dest_slug: str) -> str:
    """Valide le sous-dossier de destination (règle #2) comme **segment simple** sous `docs/design/` : ni
    vide, ni séparateur, ni `..`, ni caché. Retourne le slug sûr (défaut appelant : `brand`)."""
    slug = dest_slug.strip().strip("/")
    if not slug or "/" in slug or "\\" in slug or ".." in slug or slug.startswith("."):
        raise UploadRejected(f"sous-dossier de destination invalide : {dest_slug!r}")
    return slug


def write_project_upload(worktree: Path, *, filename: str, data: bytes,
                         dest_slug: str = "brand") -> Path | None:
    """Écrit `data` sous `<worktree>/docs/design/<dest_slug>/<filename>` et retourne le chemin écrit.
    **No-op** (retourne `None`, rien écrit ni validé) si `data` est vide — symétrie avec `write_design_seed`
    (pas d'asset vide). Sinon applique les bornes verrouillées (secret #6 → `UploadRejected` ; type #5 →
    `UploadTypeRejected` ; taille #5 → `UploadTooLarge` ; nom/traversal #7 → `UploadRejected`) **avant**
    d'écrire un octet. PUR (aucune horloge, aucun réseau)."""
    if not data:
        return None
    name = _validate_filename(filename)
    slug = _validate_dest_slug(dest_slug)
    if len(data) > _UPLOAD_MAX_BYTES:
        raise UploadTooLarge(
            f"fichier trop volumineux : {len(data)} o > {_UPLOAD_MAX_BYTES} o "
            f"(borne _UPLOAD_MAX_BYTES). Aucune troncature — réduis le fichier ou passe par un autre canal.")
    dest = Path(worktree).joinpath(*_DEST_ROOT, slug)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    # Défense en profondeur (règle #7) : le chemin résolu reste sous `<worktree>/docs/design/`.
    root = Path(worktree).joinpath(*_DEST_ROOT).resolve()
    if not target.resolve().is_relative_to(root):
        raise UploadRejected(f"chemin hors de docs/design/ (path-traversal) : {filename!r}")
    target.write_bytes(data)
    return target
