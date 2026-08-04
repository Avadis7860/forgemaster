// server.mjs — page d'amorçage semée par le forgemaster (P3). Sert un 200 out-of-the-box via le module http
// natif de Node (zéro dépendance) pour qu'un projet front frais soit déployable SANS édition manuelle.
// Générique (aucun nom de projet en dur) : remplace-le par ton vrai front (build Vite + serveur
// statique) au fil des features, en gardant l'écoute sur 0.0.0.0:8000 — c'est le contrat compose
// (le forgemaster publie ${FORGEMASTER_PORT} → 8000).
import { createServer } from "node:http";

const PORT = 8000;
const PAGE = `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>forgemaster — amorçage</title></head>
<body><main><h1>front — amorçage</h1>
<p>Projet semé par le forgemaster. Remplace ce stub par ton front.</p></main></body></html>`;

createServer((_req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(PAGE);
}).listen(PORT, "0.0.0.0");
