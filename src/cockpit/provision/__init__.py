"""provision — policy de provisioning : le « Toolkit auto-travaillable » semé dans le SoT de tout projet
créé par le cockpit. Le CONTENU (payload générique, zéro spécifique-projet) est **vendoré** sous `payload/` ;
ce module le charge en mapping `chemin-relatif → contenu`.

La **policy** (quel payload semer) vit ici, PAS dans `git/internal.py` (qui reste primitives git seules) :
`projects/registry.create_project` lit ce payload et le passe à `InternalGit().init_sot(sot, payload=…)`.
Déterministe : lecture de fichiers locaux vendorés, ordre trié, aucune I/O réseau, aucun LLM.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

_PAYLOAD_DIR = Path(__file__).parent / "payload"


def _walk_files(base: Path) -> Iterator[Path]:
    """Parcourt récursivement `base`, feuilles d'abord ou non peu importe, ordre **trié** par nom à chaque
    niveau (déterministe). `iterdir()` inclut les dotfiles (`.docsmap.toml`, `.claude/`) — voulu."""
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry


def load_payload() -> dict[str, str]:
    """Le toolkit auto-travaillable en mapping `chemin-relatif-POSIX → contenu texte`. Générique (aucun
    spécifique-projet), lu depuis le payload vendoré. Répertoire absent → mapping vide (fail-soft)."""
    if not _PAYLOAD_DIR.is_dir():
        return {}
    return {
        f.relative_to(_PAYLOAD_DIR).as_posix(): f.read_text(encoding="utf-8")
        for f in _walk_files(_PAYLOAD_DIR)
    }
