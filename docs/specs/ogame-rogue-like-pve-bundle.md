# spec — Split browser-game + bundle crash-test `ogame-rogue-like-pve` (P0 : décision + design)

> **Statut** : ⛔ **SUPERSEDED** (2026-07-24) — l'approche « bundle-type ogame **hand-codé** » décrite ici a
> été **défaite** (unwind). Le spécialisé n'est PAS un bundle-type mais un **STYLE distillé en capital servi**
> (blueprint + templates, `mcp-catalogs-data`), **énuméré** par l'interview first-session du générique
> `browser-game` (le worker construit le jeu lui-même). Coder le jeu à la place du worker vidait le crash-test
> et gaspillait le capital. Ce fichier est conservé comme **historique du pivot capital-jeton** (tracker vault
> `cockpit-browser-game-generic-vs-ogame-bundle` + doctrine capital-jeton). Tout ce qui suit est l'état
> d'origine, **non maintenu**.
> **Portée** : sépare le type `browser-game` (aujourd'hui confondu générique+ogame) en **deux types
> indépendants** — `browser-game` (générique neutre) + `ogame-rogue-like-pve` (bundle **crash-test** : jeu
> ogame **fini né-avec**). Cible : `provision/bundles/types/{browser-game,ogame-rogue-like-pve}/`,
> `provision/derive/` (+ de-hardcode `derive.py`), `tests/test_provision.py`.

## Problème tranché

Le type `browser-game` **confond deux objets de capital distincts** :

1. un **squelette TS-mono runnable** (schéma Zod partagé + `tick` déterministe + serveur Hono autoritatif WS +
   client React + gate `eslint→tsc→vitest`) — quasi-**générique** ;
2. une **identité ogame-rogue-like-PvE** portée **uniquement dans la prose** (`CLAUDE.md §1` « jeu de gestion
   PvE vs bots, OGame-like/roguelike », patron d'étapes du blueprint splicé par `derive.py:151`, refs
   `browser-game-pve`).

Résultat : un générique **pollué** d'ogame-ité aspirationnelle *et* un ogame **famélique** en code (le
« jeu » né-avec n'est qu'un compteur de ressources qui monte). Les deux besoins tirent en sens opposés — un
**type** bundle doit être **générique** (réutilisable pour toute la classe) ; un genre spécifique riche
mérite **son** objet.

**Décision de bosse (2026-07-24)** : le spécialisé n'est pas un type-générique-de-plus mais un **bundle
crash-test** — il livre un **jeu ogame fini né-avec** (reproduit fidèlement à partir des vraies données wiki +
serveurs privés + notre touche perso roguelike-PvE), avec room pour customiser. Rationale : c'est le **vecteur
E2E maximal** — peu de projets exercent l'arc *scaffold → roadmap → projet fini* aussi loin ; on l'exploite
pour débusquer bugs/manques/améliorations **avant une sortie officielle**. La neutralité s'applique au
**générique**, jamais au crash-test.

## Règles verrouillées

1. **Deux types INDÉPENDANTS, pas générique+dérive.** La machinerie runtime compose déjà `generic-base ⊕
   overlay(type)` (whole-file), **sans** composition type-sur-type ; le mécanisme `derive/` est **build-time**.
   Chacun est un overlay complet et autonome ; le squelette TS-mono est partagé en **idiome** (mêmes configs,
   même contrat de gate), le code de jeu **diverge par design**.
2. **`browser-game` = générique NEUTRE.** Aucun genre imposé : `CLAUDE.md §1` décrit « un jeu navigateur,
   serveur-autoritatif, tick déterministe — **tu** définis le genre » ; pas de patron d'étapes ogame ; jeu
   placeholder neutre (le compteur de ressources runnable suffit comme base honnête). Starter pour démarrer
   **n'importe quel** jeu navigateur.
3. **`ogame-rogue-like-pve` = bundle crash-test COMPLET.** Overlay opinionné livrant un **jeu fini né-avec** :
   moteur ogame quasi-complet (économie, flotte, combat, bots/factions, map, structure roguelike, persistance)
   + contenu jouable. Un projet semé est **immédiatement jouable** ; le worker **thème / équilibre / étend**
   (customisation = « notre touche perso » exposée, pas une refonte). Reste un **type discoverable**, valide
   `validate_bundle`, passe la suite dynamique `test_provision.py` (structure : `CLAUDE.md §1-6`, persona,
   `corpus=true`, `docs/architecture.md` non-stub, README par-dossier, facettes adossées).
4. **Serveur-autoritatif + déterminisme, hérités et non re-débattus.** L'état canonique et sa résolution
   vivent côté serveur (le client propose, le serveur dispose) ; toute la simulation est **déterministe par
   seed** (rejeu byte-identique). Aucune horloge murale (`Date.now()`), aucune itération de map non-ordonnée,
   math **floorée** comme l'ogame réel (retire le bruit flottant bas de gamme). PRNG **counter-based** adressable
   (`rng(runSeed, "battle", battleId)`) — pas d'état mutable filé partout.
5. **De-hardcode `derive.py`.** La référence blueprint `browser-game-pve` splicée en dur dans le §6 du
   `CLAUDE.md` dérivé (`derive.py:~151`) devient un **paramètre de `values.toml`** (`[template].blueprint_ref`
   ou équivalent). Chaque type qui porte un `derive/` nomme **son** blueprint ; aucun ref en dur dans le code.
6. **Formules = sourcées, jamais « de mémoire ».** Le moteur reproduit les vraies formules ogame (production,
   coûts, temps de construction, combat, carburant) avec **sources citées** (voir §Design). Le **contenu neutre**
   du générique et le **contenu jouable** du crash-test sont deux régimes explicites, jamais mélangés.

## Design du moteur `ogame-rogue-like-pve` (born-with quasi-complet)

> Référence de domaine complète (toutes les tables/formules + sources) → graduera dans le
> `bundles/types/ogame-rogue-like-pve/docs/design.md` du bundle à P2 (foyer du game-design lu par un projet
> semé). Ci-dessous : l'ossature load-bearing et les décisions de design.

### Systèmes (tous né-avec, contenu neutre paramétrable)

- **Ressources & économie** : métal / cristal / deutérium (dépensables) + énergie (balance). Production/h par
  bâtiment `= base · L · 1.1^L` (métal 30, cristal 20, deut 10·(−0.002·T+1.28)) ; énergie consommée
  `10·L·1.1^L` (deut synth 20·) ; solaire `20·L·1.1^L` ; déficit d'énergie ⇒ mines scalées au ratio. Stockage
  `5000·⌊2.5·e^(20L/33)⌋`. Bonus multiplicatifs (Plasma-tech, officiers, classes).
- **Bâtiments** : coût `base·factor^(L−1)` (mines 1.5/1.6, reste ×2) ; temps `= (M+C)/(2500·(1+Robotics)·
  2^Nanite·UniSpeed)`. Arbre de prérequis (robotics→shipyard/nanite, lab→recherches).
- **Recherche** : 16 techs `base·2^(L−1)` (astro 1.75) ; armes/bouclier/blindage `+10%/niveau` (multiplicatifs
  sur les stats de combat) ; drives (combustion/impulsion/hyperespace) pilotent temps de vol + carburant.
- **Flotte & défense** : coque `= (M+C)/10`, bouclier/arme de base, cargo/vitesse/carburant + drive-tech. Tables
  complètes (small/large cargo, LF/HF, cruiser, battleship, battlecruiser, bomber, destroyer, deathstar,
  recycler, sonde ; lanceur/laser/ion/gauss/plasma, dômes, missiles).
- **Combat (le système clé) — résolveur déterministe** : ≤ 6 rounds ; chaque round, boucliers **régénérés**,
  toutes les unités tirent sur une cible aléatoire ; **bounce** si `arme < 1% du bouclier` ; absorption bouclier
  puis coque ; **rapidfire** relance le tir avec proba `(r−1)/r` ; **explosion** sous 70 % de coque avec proba
  `1 − coque/coque₀` par coup. Post-combat : débris (30 % M+C des détruits, config), lune `min(⌊débris/1e5⌋,20)%`,
  réparation défense (~70 %, config), pillage ≤ 50 %. Les **seuls** tirages aléatoires (cible, rapidfire,
  explosion, réparation, lune) → PRNG **battle-scoped** seedé (`hash(runSeed, attackers, defenders, coords,
  tick)`), itération sur **listes triées stables**.
- **Mouvement de flotte** : coordonnées `galaxie:système:position` ; distance (inter-galaxie 20000·Δg ;
  intra-galaxie 2700+95·Δs ; intra-système 1000+5·Δp) ; temps de vol `(10 + (35000/vitesse%)·√(10·dist/v))/
  UniSpeed` ; carburant ∝ `base·N·(dist/35000)·(vitesse%/100+1)²`. Missions : attaque / transport / déploiement /
  espionnage / colonisation / recyclage / **expédition** (nœuds à récompense seedée).
- **Structure roguelike + persistance** : voir §Touche perso. Persistance SQLite/Drizzle câblée (le crash-test
  va jusque-là — c'est justement ce qu'on veut stresser).

### Notre touche perso — le twist roguelike-PvE (choix de design P0, ouvert au GO)

L'ogame est né multijoueur temps-réel-sur-des-jours ; on **garde le jeu de formules** et on **remplace le
pacing et les adversaires** :

- **Un run = un univers seedé** : le seed génère galaxie, home (temp/champs), et les **empires PNJ**
  (placement + archétype), les nœuds d'expédition, le timing des événements. Même seed ⇒ univers identique
  (daily-challenge friendly, rejeu de debug).
- **Temps compressé en ticks** : 1 tick ≈ 1 « heure » de prod ; vols/constructions/recherches en *N ticks*
  (les mêmes formules, UniSpeed élevé) → un run se joue en **30–90 min**, pas en semaines.
- **Adversaires = factions PNJ** (agents économiques à états, faisant tourner **le même moteur**), archétypes :
  *Farmer* (riche/peu défendu → cible de raid), *Raider* (t'attaque sur un timer qui monte → la pression),
  *Turtle* (défense lourde sur un cache → puzzle de siège), *Expansionist* (colonise → timer mou), *Boss*
  (empire deathstar-tier qui grossit → condition de victoire). Croissance PNJ = **courbe déterministe** du seed.
- **Meta-progression** (rogue-**lite**) : une monnaie permanente (renommer le *Dark Matter*) gagnée aux
  expéditions/boss → débloque tech de départ, bonus passifs, loadouts, **classes** (Collector/General/Discoverer
  = « personnages ») et **reliques** (+50 % débris, rapidfire +1…). Survit à la permadeath.

**Reco P0** : cœur **« Raid Economy »** (raid des farms → compounding du capital, pression Raider montante) +
**Boss doom-clock** comme condition de victoire + permadeath & meta-progression. Les deux réutilisent les
formules **quasi verbatim**, ne demandent **qu'un** résolveur de combat déterministe + des build-orders PNJ
scriptés, et donnent un win/loss propre. Les nœuds d'expédition (structure Slay-the-Spire) se superposent comme
**contenu** plus tard. *(Alternatives écartables au GO : Seeded Siege pur, Expedition Crawler, 4X-lite.)*

### Frontière born-with / worker (préserve le sens de l'E2E)

Le bundle livre **le moteur complet + un run jouable** (contenu neutre-mais-réel). Le worker d'un projet
**thème** (noms/factions/lore), **équilibre** (constantes de config), **ajoute du contenu/largeur** (archétypes,
reliques, nœuds, modes). Un jeu fini né-avec ≠ *rien à faire* : c'est un substrat riche à customiser — plus
représentatif du vrai usage que « partir de zéro », et l'E2E void-runner exerce alors la forge sur un projet de
**magnitude maximale** (gate/build/deploy/UI/persistance sous vraie charge).

## Invariants de test (à encoder — P1/P2)

- **Générique neutre** : `load_bundle("browser-game")` — `CLAUDE.md` **sans** « ogame »/« roguelike »/patron
  d'étapes ogame ; aucun ref `browser-game-pve`. Suite dynamique verte (type discoverable).
- **Type spécialisé** : `load_bundle("ogame-rogue-like-pve")` discoverable, `validate_bundle` vert, suite
  dynamique verte (§1-6, persona, `corpus=true` à travers l'override whole-file, `docs/architecture.md`
  non-stub, README par-dossier, facettes adossées). Toolchain prouvée : entrée dans `_TYPE_TOOLCHAIN_PROBES`.
- **De-hardcode `derive.py`** : le §6 dérivé nomme le blueprint depuis `values.toml`, pas un littéral ; test
  paramétré sur le ref déclaré.
- **Déterminisme du moteur** (P2) : même seed ⇒ combat/run **byte-identiques** (rejeu) ; seed différent ⇒
  trajectoire différente ; réducteurs purs (aucune mutation d'entrée) ; aucune horloge murale dans la sim.
- **Formules** (P2) : tests-oracle sur les valeurs sourcées (production niveau L, coût, temps de construction,
  round de combat de référence).

## Phases (GO humain par phase — fail-closed)

- **P0** — décision + design (ce doc). *(livrée)*
- **P1** — `browser-game` **neutralisé** + de-hardcode `derive.py` (ref → `values.toml`). Suite dynamique verte.
- **P2** — type `ogame-rogue-like-pve`, moteur quasi-complet, **sous-features gatées** : (2a) scaffolding du type ;
  (2b) modèle de domaine riche ; (2c) combat + IA bots ; (2d) roguelike + persistance ; (2e) UI de gestion +
  boucle visuelle ; (2f) Docker/compose + docs non-stub + probe toolchain. Gate complet vert.
- **P3** — distillation (enseignements ingestion/création de bundles → specs) ; graduation blueprint ogame dans
  `mcp-catalogs-data` si réutilisable prouvé ; clôture tracker vault + post-mortem.

## Sources (domaine)

OGame Fandom wiki (Formulas, Combat, Rapid Fire, Distance, Fuel, Buildings, Ships, Technology, Storage,
Expedition/Logs) ; compendium communautaire (gameguidegameguide.blogspot.com) ; moteurs de combat OPBE
(`github.com/jstar88/opbe`) + ogame-fleet-optimizer ; NamuWiki (combat) ; pages officielles Gameforge. Détail
complet + variantes contestées (terme carburant moderne, constantes débris/réparation par-univers, matrice
rapidfire complète) → graduera dans `docs/design.md` du bundle à P2 (2ᵉ passe d'exactitude production notée).
