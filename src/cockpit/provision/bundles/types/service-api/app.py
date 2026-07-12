"""app.py — service d'amorçage semé par le cockpit (P3). Répond 200 out-of-the-box pour qu'un projet
frais soit déployable **sans édition manuelle**. Générique (aucun nom de projet en dur) : remplace-le par
ton vrai service (FastAPI, etc.) au fil des features, en gardant l'écoute sur 0.0.0.0:8000 — c'est le
contrat compose (le cockpit publie ${COCKPIT_PORT} → 8000)."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDR = ("0.0.0.0", 8000)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"service": "cockpit", "status": "ok", "stage": "amorçage"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(ADDR, Handler).serve_forever()
