"""routes — routers FastAPI découpés par domaine (projects / roadmap / dispatch / gate), montés par
`daemon.app.build_app` via injection explicite. Chaque router délègue au cœur (transport local), sans
tape de module-global (correctif #1) ; le monolithe orchestrator 1650-LOC est éclaté ici (correctif #3).

Statut : scaffoldé — les routers concrets seront ajoutés à la phase logique (un fichier par domaine)."""
from __future__ import annotations
