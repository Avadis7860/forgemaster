"""ids — identifiants du forgemaster : slugs kebab-case validés (anti-injection : un slug finit dans un nom
de branche / chemin de worktree / commande git) et uuid générés.

Kebab strict, miroir de la discipline vault (`git_ops._SAFE_BRANCH_SEGMENT`) : segments alphanumériques
minuscules séparés par `-`, ni `..` ni `/` (ceux-là appartiennent au nom de branche COMPLET, pas au slug).
"""
from __future__ import annotations

import re
import uuid

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_slug(value: str) -> bool:
    """Vrai ssi `value` est un slug kebab-case strict (utilisable tel quel dans branche/chemin/commande)."""
    return bool(_SLUG.fullmatch(value or ""))


def ensure_slug(value: str, *, field: str = "slug") -> str:
    """Retourne `value` s'il est un slug valide, sinon lève `ValueError` (fail-closed, anti-injection)."""
    if not is_slug(value):
        raise ValueError(f"{field} invalide (kebab-case attendu, ex. 'ma-feature') : {value!r}")
    return value


def new_id() -> str:
    """Un identifiant opaque unique (uuid4). Généré, jamais un littéral (traçabilité + zéro collision)."""
    return str(uuid.uuid4())
