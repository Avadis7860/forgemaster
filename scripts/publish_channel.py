#!/usr/bin/env python3
"""publish_channel.py — produit le `channel.json` signé d'une édition. **Geste de mainteneur.**

Il vit dans `scripts/` et **pas** dans le wheel : ce qu'une édition distribuée sait faire d'un manifeste,
c'est le vérifier. Le producteur n'a rien à faire chez celui qui reçoit.

**La clé privée arrive sur l'entrée standard**, jamais en `argv` (visible dans `ps` de toute la machine),
jamais en variable d'environnement (héritée par tout ce qu'on lance ensuite), jamais en fichier. Ce script
ne résout aucun secret — le coffre est le seul résolveur, et il reste dans le vault :

    .claude/scripts/.venv/bin/python .claude/scripts/bws_secret.py <uuid> --raw \\
      | python scripts/publish_channel.py --wheel dist/forgemaster-<v>-py3-none-any.whl --out channel.json

**La preuve est prise ici, pas plus tard** : l'enveloppe produite est immédiatement relue et vérifiée
**contre la racine de confiance embarquée dans le wheel qu'on annonce**. C'est le seul endroit où les deux
moitiés se rencontrent avant qu'un utilisateur ne les rencontre. Sans ce contrôle, on peut publier une
annonce parfaitement signée que pas une édition au monde n'accepte — et le symptôme, chez l'utilisateur,
serait `unverified` : le pire verdict, celui qui apprend à ignorer l'alarme.

Codes de sortie : 0 = écrit et vérifié · 1 = refus (rien n'est écrit) · 2 = usage.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forgemaster import channel_publish, update_channel  # noqa: E402


def lignee(sha: str, *, repo: Path, plafond: int) -> list[str]:
    """L'**ascendance** de l'édition annoncée, du plus récent au plus ancien, bornée au plafond.

    Une ascendance, et non la liste des éditions publiées : la question posée par le client est *l'édition
    annoncée descend-elle de ce que tu exécutes ?*, et une instance exécute un **commit** — y compris un
    wheel bâti maison depuis un commit jamais publié. La liste des éditions publiées ne situerait que ceux
    qui ont installé une édition publiée, c'est-à-dire personne le premier jour ; et « instance plus
    ancienne que la fenêtre », l'une des trois causes que la spec nomme, ne veut rien dire autrement.

    `--first-parent` : on suit la ligne de `main`, pas les côtés des merges qui y ont atterri.
    """
    out = subprocess.run(["git", "-C", str(repo), "rev-list", "--first-parent",
                          f"-n{plafond}", f"{sha}^"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"✗ lignée non mesurable ({out.stderr.strip()}) — le commit {sha[:12]} est-il "
                         f"dans ce dépôt ? Une annonce sans lignée ne situe personne.")
    return [ligne for ligne in out.stdout.split() if ligne]


def _privee() -> bytes:
    if sys.stdin.isatty():
        raise SystemExit("✗ aucune clé sur l'entrée standard — ce script ne va PAS la chercher lui-même. "
                         "Passe-la en pipe depuis le coffre (cf. l'en-tête de ce fichier).")
    texte = sys.stdin.read().strip()
    if not texte:
        raise SystemExit("✗ entrée standard vide — le coffre a-t-il rendu quelque chose ?")
    try:
        return base64.urlsafe_b64decode(texte.encode("ascii"))
    except Exception:                                    # noqa: BLE001 — la valeur ne doit PAS fuiter ici
        raise SystemExit(f"✗ la valeur lue ({len(texte)} caractères) n'est pas du base64url — mauvais "
                         f"secret, ou un pipe qui a ajouté quelque chose.") from None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Produit le channel.json signé d'une édition.")
    ap.add_argument("--wheel", required=True, type=Path, help="le wheel ANNONCÉ (celui qu'on publiera)")
    ap.add_argument("--out", required=True, type=Path, help="où écrire le channel.json")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                    help="dépôt où mesurer la lignée (défaut : ce checkout)")
    ap.add_argument("--published-at", help="horodatage ISO 8601 (défaut : maintenant, UTC)")
    args = ap.parse_args(argv)

    if not args.wheel.is_file():
        raise SystemExit(f"✗ {args.wheel} introuvable")
    prive = _privee()

    with zipfile.ZipFile(args.wheel) as z:
        try:
            keys_brut = z.read(channel_publish.WHEEL_KEYS)
        except KeyError:
            raise SystemExit(
                f"✗ {channel_publish.WHEEL_KEYS} absent du wheel — cette édition n'embarque AUCUNE racine "
                f"de confiance : elle ne pourra vérifier aucune annonce, y compris celle-ci. Rien publié."
            ) from None
        sha = json.loads(z.read(channel_publish.WHEEL_STAMP))["sha"]

    annonce = channel_publish.build_announce(
        args.wheel,
        lineage=lignee(sha, repo=args.repo, plafond=update_channel.LINEAGE_MAX),
        published_at=args.published_at or datetime.now(UTC).isoformat(timespec="seconds"))
    payload = json.dumps(annonce, sort_keys=True, separators=(",", ":")).encode("utf-8")
    enveloppe = channel_publish.sign_envelope(payload, prive)
    fil = json.dumps(enveloppe, indent=2, sort_keys=True) + "\n"

    # Le contrôle qui compte : relire ce qu'on vient d'écrire AVEC LE CODE DU CLIENT, sous la racine de
    # confiance QUE CE WHEEL EMBARQUE. Sans lui, ces deux moitiés ne se rencontreraient que chez
    # l'utilisateur, et le symptôme y serait `unverified` — le pire verdict, celui qui apprend à ignorer
    # l'alarme.
    with tempfile.TemporaryDirectory() as tmp:
        depuis_le_wheel = Path(tmp) / update_channel.KEYS_FILE
        depuis_le_wheel.write_bytes(keys_brut)
        try:
            keys = update_channel.trust_root(depuis_le_wheel)
            relu = update_channel.verify_envelope(update_channel.parse_envelope(fil.encode()), keys)
            update_channel.parse_announce(relu)
        except update_channel.ChannelError as exc:
            raise SystemExit(
                f"✗ l'annonce produite ne vérifie PAS sous la clé que ce wheel embarque : {exc}\n"
                f"  Rien n'a été écrit. C'est le contrôle qui existe pour que l'utilisateur ne soit pas "
                f"le premier à le découvrir.") from None

    args.out.write_text(fil, encoding="utf-8")
    ed = annonce["edition"]
    print(f"✓ {args.out} — édition {ed['version']} @ {ed['sha'][:12]} · lignée {len(annonce['lineage'])} "
          f"· signé par {enveloppe['signatures'][0]['key_id']}", file=sys.stderr)
    print(f"  vérifié avec le code du CLIENT, sous la racine embarquée dans {args.wheel.name}.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
