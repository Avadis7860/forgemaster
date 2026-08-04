"""identity — identité git DÉTERMINISTE par projet. PUR. Source UNIQUE de l'auteur git d'une écriture git
automatisée : `<projet>-<base>-<rôle>` (kebab), dérivé au runtime — jamais un littéral projet.

Port de `lib/worker_identity.py` (resolve_identity). Deux consommateurs (d'où le placement neutre sous
`git/`, aucun cycle) : le **commit du travail worker** (`dispatch`, rôle `worker`) et le **writeback de
merge** (`gate/merge`, rôle `writeback`). Injectée le temps de l'op (spec merge-writeback : identité ≠
credential, aucun secret par acteur) ; un projet géré obtient ses identités sans une ligne de code
(généricité). Anti-injection : les valeurs finissent dans un env git → on REJETTE tout segment non-kebab
(fail-loud), on ne maquille pas une entrée douteuse.
"""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")   # kebab strict (même famille que core.ids)
_EMAIL_DOMAIN = "worker.local"                        # domaine fictif stable

# Fallback générique : SEULEMENT quand aucun contexte projet n'est fourni (smokes / writeback anonyme).
_GENERIC: dict[str, dict[str, str]] = {
    "worker": {"name": "worker-dev", "email": "dev@worker.local"},
    "meta": {"name": "meta-worker", "email": "dev@meta.local"},
    "writeback": {"name": "vault-writeback", "email": "dev@writeback.local"},
}


def _slug(value: str | None, field: str) -> str:
    v = (value or "").strip().lower()
    if not _SLUG_RE.fullmatch(v):
        raise ValueError(f"{field} non kebab-case (anti-injection) : {value!r}")
    return v


def resolve_identity(project: str | None, base: str | None, *, role: str = "writeback") -> tuple[str, str]:
    """Identité git `(name, email)` d'une écriture d'orchestration. `project` fourni → identité dérivée
    `<projet>-<base>-<rôle>` (segments validés kebab) ; absent → fallback générique par rôle. PUR."""
    if not project:
        if role not in _GENERIC:
            raise ValueError(f"rôle inconnu sans contexte projet : {role!r}")
        g = _GENERIC[role]
        return (g["name"], g["email"])
    name = f"{_slug(project, 'project')}-{_slug(base, 'base')}-{_slug(role, 'role')}"
    return (name, f"{name}@{_EMAIL_DOMAIN}")
