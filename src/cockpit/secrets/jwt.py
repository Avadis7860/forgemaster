"""secrets.jwt — JWT HS256 en STDLIB pure (mint + verify), sans dépendance.

Port fidèle de `jwt_stdlib` du vault (source unique côté framework) : pas de `pyjwt`, utilisable partout.
Garanties : anti alg-confusion (rejette `alg≠HS256`, donc `none`/asym), signature HMAC en comparaison
constant-time, contrôle `exp`/`aud`/`iss`. Claims `sub/iss/aud/iat/exp`.

Seul consommateur aujourd'hui : `provision.mcp` (mint du Bearer pour le `.mcp.json` injecté au dispatch). Le
contrat de claims (`aud`, `iss`) est celui **validé par le serveur forgemaster-catalogs** — cf.
`provision.mcp`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

_MIN_SECRET_LEN = 32  # HS256 exige un secret ≥32 caractères (sinon la signature est faible).


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_hs256(subject: str, secret: str, *, audience: str, issuer: str = "vault-mcp",
               ttl_seconds: int = 3600) -> str:
    """Forge un JWT HS256 (claims `sub/iss/aud/iat/exp`). Lève `ValueError` si `secret` < 32 caractères."""
    if len(secret) < _MIN_SECRET_LEN:
        raise ValueError(f"secret trop court ({len(secret)}c) : HS256 exige ≥{_MIN_SECRET_LEN} caractères.")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": subject, "iss": issuer, "aud": audience, "iat": now, "exp": now + ttl_seconds}
    head = b64url_encode(json.dumps(header).encode())
    body = b64url_encode(json.dumps(payload).encode())
    signing_input = f"{head}.{body}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url_encode(sig)}"


def verify_hs256(token: str, secret: str, *, audience: str, issuer: str | None = None,
                 leeway: int = 0) -> dict | None:
    """Vérifie un JWT HS256. Retourne les claims si valide, sinon `None` (alg/signature/exp/aud/iss)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h_b64, p_b64, s_b64 = parts
    try:
        header = json.loads(b64url_decode(h_b64))
        if header.get("alg") != "HS256":          # anti alg-confusion (rejette none/asym)
            return None
        sig = b64url_decode(s_b64)
        claims = json.loads(b64url_decode(p_b64))
    except Exception:  # noqa: BLE001 — token malformé → invalide
        return None
    expected = hmac.new(secret.encode("utf-8"), f"{h_b64}.{p_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None
    now = int(time.time())
    if "exp" in claims and now > int(claims["exp"]) + leeway:
        return None
    if audience is not None and claims.get("aud") != audience:
        return None
    if issuer is not None and claims.get("iss") != issuer:
        return None
    return claims
