# multi-os — portabilité du cockpit

**WSL-first** : l'édition lightweight tourne par défaut sous WSL (substrat de l'édition lightweight,
north-star v3), Debian et macOS en cibles secondaires. Le cockpit orchestre des process locaux et une base
SQLite — pas de dépendance OS exotique, mais quelques points de vigilance.

| Risque cross-OS | Mesure |
|---|---|
| Fins de ligne (git `core.autocrlf`) faussent hash/diff | `.gitattributes` : `* text=auto eol=lf` + `*.yaml/*.jsonl eol=lf`. Le même source → même comportement partout. |
| Chemins Windows vs POSIX | Chemins **POSIX** partout ; `pathlib.Path` ; `core.fs.safe_path` borne à une racine POSIX. Jamais de séparateur `\`. |
| SQLite : verrouillage réseau / WAL sur montage | Base sous `COCKPIT_HOME` (FS local, pas un partage réseau) ; `journal_mode=WAL` pour la concurrence CLI↔daemon locale. |
| `flock` (sérialisation worktree) indisponible/varie | `flock` sur un fichier du `.git` partagé — POSIX (Linux/macOS/WSL) ; documenter si une cible sans `flock` apparaît. |
| Worker `claude` / `git` absents du PATH | Résolus via PATH au runtime ; `core.run` capture l'échec proprement (rc≠0, jamais un faux-vert). |
| PTY (terminal web) : API `pty`/`os.openpty` POSIX | Terminal intégré = cible POSIX (WSL/Debian/macOS). Windows natif hors périmètre V1. |

## Matrice de support

| OS | Statut | Notes |
|---|---|---|
| **WSL (Ubuntu/Debian)** | cible primaire | environnement de dev de référence |
| **Debian/Linux natif** | supporté | à valider (mêmes primitives POSIX) |
| **macOS** | best-effort | POSIX ; `flock`/`pty` OK ; non testé en continu |
| **Windows natif** | hors périmètre V1 | pas de PTY/flock POSIX ; utiliser WSL |

## Checklist de portabilité (avant de livrer une couche)

- [ ] Aucun chemin absolu d'hôte en dur (tout via `config` / `safe_path(root=…)`).
- [ ] Aucune commande shell supposant un OS (préférer argv liste via `core.run`, pas de `shell=True` gratuit).
- [ ] Séparateurs POSIX ; `pathlib` pour composer les chemins.
- [ ] Un binaire externe manquant (`git`, `claude`) → erreur claire (rc≠0), jamais un faux-vert.
