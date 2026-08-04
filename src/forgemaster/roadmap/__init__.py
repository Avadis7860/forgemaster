"""roadmap — la roadmap in-repo d'un projet (features + tasks) et son résolveur DAG. `model` sérialise
`.forgemaster/roadmap.yaml` (versionné avec le projet) ↔ DB ; `resolver` dérive le séquencement (spec
task-next-resolver-dag)."""
from __future__ import annotations
