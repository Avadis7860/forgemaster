"""daemon — le daemon FastAPI : une **vue par-dessus le cœur** (la spine reste CLI + cœur déterministe).
`app.build_app(settings)` câble les routes par **injection explicite** (correctif #1 : plus de god-module
`import server`) ; les routers sont découpés par domaine (correctif #3 : fin du monolithe 1650-LOC)."""
from __future__ import annotations
