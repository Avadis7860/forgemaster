# Méthode — facette Frontend (browser-game)

1. **Boucle visuelle** — tout changement d'écran : screenshot **puis Read** de la capture AVANT de livrer.
   Ambigu (« façon X ») → mockup A/B d'abord.
2. **Preuve de rendu (Tier-1.5)** — émets `.cockpit/verify-markers.json`.
   - **Écran at-rest** (menu, tableau, réglages) : `{"markers":[…]}` = les chaînes FR **littérales** que ton
     écran rend (titres, labels de ton `acceptance`). La gate cherche ces marqueurs dans le DOM du
     preview-deploy — déclare le vrai, pas un vœu (un marqueur non rendu ⇒ gate rouge).
   - **Jalon jouable** (« jouable = observable **après un geste** ») : un écran statique ne prouve rien.
     Ajoute un bloc `interaction` à DEUX temps —
     ```json
     {
       "markers": ["Compteur", "Jouer un tour"],
       "interaction": {
         "clicks": [{"text": "Jouer un tour"}],
         "after_markers": ["Tour 1"],
         "wait_for_text": "Prêt"
       }
     }
     ```
     `markers` = le **cadre at-rest** (rendu au chargement) ; `clicks` = gestes **strictement READ-ONLY**
     (ouvrir/jouer un tour — **JAMAIS** submit/delete/dispatch : on **prouve** que le jeu réagit, on ne joue
     pas la partie) ; `after_markers` = une ou des chaînes FR qui **n'existent qu'APRÈS** le geste (un compteur
     qui avance, « Tour N »). La gate joue les clics, puis exige que `after_markers` apparaissent **et**
     qu'ils étaient **absents at-rest** — un `after_marker` déjà présent au chargement ⇒ gate **rouge**
     (transition non prouvée : c'est un label statique, pas une preuve d'interaction). `wait_for_text`
     (optionnel) cale l'état at-rest stable avant la capture (contenu piloté WebSocket, arrivé après
     networkidle).
3. **Design-system d'abord** — `frontmap where` (tokens / primitives / routes) avant de créer du neuf.
4. **Serveur-autoritatif** — l'UI lit l'état (React Query poll + WebSocket events), envoie des commandes
   validées par des schémas **Zod partagés** ; jamais de règle de jeu calculée côté client.
5. **Doc-first (anti-boucle)** — avant un import non trivial (React Query / Zod), interroge le MCP
   (`query(type=tech, scope=browser-game)`) — pas de signature inventée.
6. **Gate** — `eslint` → `tsc` → `vitest` vert. Corrige la cause.
7. **Fraîcheur** — front touché → `frontmap build` (+ `codemap build` pour la logique partagée).
